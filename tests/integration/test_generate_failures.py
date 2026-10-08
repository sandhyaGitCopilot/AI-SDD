"""T024: every early-exit path of `generate` (contracts/cli.md, SC-008).

Two properties are asserted throughout, because both are promises the spec makes:

* the right exit code, so scripts can branch (FR-028)
* **no draft left behind** on the paths that promise none (FR-004, FR-031)

A half-written draft after a failure would be worse than no draft: the user would review and
approve content the tool already knows is incomplete.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from jira_testgen.commands.generate import run_generate
from jira_testgen.errors import (
    AuthFailure,
    GenerationFailure,
    InsufficientContent,
    InvalidArguments,
    IssueForbidden,
    IssueNotFound,
    MissingProjectPermission,
    SiteConfigUnresolved,
)

from .conftest import adf, default_payload

pytestmark = pytest.mark.integration


def attempt(tmp_path: Path, stub: Any, **kwargs: Any) -> None:
    options: dict[str, Any] = {
        "issue_key": "PROJ-123",
        "target_project": None,
        "ac_field": None,
        "issue_type": None,
        "link_type": None,
        "max_cases": 25,
        "workspace": tmp_path / ".jira-testgen",
        "no_wait": True,
        "json_output": False,
        "yes": False,
        "generation_client": stub,
    }
    options.update(kwargs)
    run_generate(**options)


def drafts_written(tmp_path: Path) -> list[Path]:
    root = tmp_path / ".jira-testgen" / "runs"
    return list(root.rglob("testcases.csv")) if root.exists() else []


class TestLocalValidation:
    @pytest.mark.parametrize("bad_key", ["not-a-key", "PROJ", "123", "PROJ-", "PROJ-12a", ""])
    def test_malformed_key_exits_2_with_no_network_call(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any, bad_key: str
    ) -> None:
        """A typo must not cost a round trip -- the shape check is local."""
        with pytest.raises(InvalidArguments) as exc:
            attempt(tmp_path, stub_generation(default_payload()), issue_key=bad_key)

        assert exc.value.exit_code == 2
        assert jira_mock.calls.call_count == 0
        assert drafts_written(tmp_path) == []

    @pytest.mark.parametrize("typed", ["proj-123", "Proj-123", "  PROJ-123  "])
    def test_lowercase_and_padded_keys_are_normalised_not_rejected(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any, typed: str
    ) -> None:
        """Deliberate: people type keys by hand, and case is not the user's mistake to pay for."""
        from jira_testgen.jira.reader import validate_issue_key

        assert validate_issue_key(typed) == "PROJ-123"

    def test_max_cases_above_cap_is_rejected_not_clamped(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """Silently clamping would mean the user asked for 50 and never learned they got 25."""
        with pytest.raises(InvalidArguments) as exc:
            attempt(tmp_path, stub_generation(default_payload()), max_cases=50)

        assert exc.value.exit_code == 2
        assert "between 1 and 25" in exc.value.message

    def test_missing_credentials_are_reported_by_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_generation: Any
    ) -> None:
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.delenv("JIRA_EMAIL", raising=False)

        with pytest.raises(InvalidArguments) as exc:
            attempt(tmp_path, stub_generation(default_payload()))

        assert "JIRA_EMAIL" in exc.value.message


class TestJiraReadFailures:
    def _issue_responds(self, jira_mock: Any, status: int) -> None:
        jira_mock.get(path__regex=r"/issue/[A-Z][A-Z0-9_]+-\d+$").mock(
            return_value=httpx.Response(status)
        )

    @pytest.mark.parametrize(
        ("status", "expected", "code"),
        [(404, IssueNotFound, 3), (403, IssueForbidden, 4), (401, AuthFailure, 5)],
    )
    def test_read_failures_have_distinct_exit_codes(
        self,
        tmp_path: Path,
        jira_mock: Any,
        stub_generation: Any,
        status: int,
        expected: type,
        code: int,
    ) -> None:
        """SC-008: a bad key and an expired token must not look the same."""
        self._issue_responds(jira_mock, status)

        with pytest.raises(expected) as exc:
            attempt(tmp_path, stub_generation(default_payload()))

        assert exc.value.exit_code == code
        assert exc.value.remediation
        assert drafts_written(tmp_path) == []

    def test_auth_failure_points_at_credentials_not_the_issue(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        self._issue_responds(jira_mock, 401)
        with pytest.raises(AuthFailure) as exc:
            attempt(tmp_path, stub_generation(default_payload()))
        assert "JIRA_API_TOKEN" in exc.value.remediation

    def test_no_generation_call_when_the_read_fails(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        self._issue_responds(jira_mock, 404)
        stub = stub_generation(default_payload())
        with pytest.raises(IssueNotFound):
            attempt(tmp_path, stub)
        assert stub.calls == [], "a failed read must not cost a model call"


class TestInsufficientContent:
    def _issue_with(self, jira_mock: Any, description: Any, summary: str = "Thin issue") -> None:
        jira_mock.get(path__regex=r"/issue/[A-Z][A-Z0-9_]+-\d+$").mock(
            return_value=httpx.Response(
                200,
                json={
                    "key": "PROJ-123",
                    "fields": {
                        "summary": summary,
                        "description": description,
                        "project": {"key": "PROJ"},
                        "issuelinks": [],
                    },
                },
            )
        )

    @pytest.mark.parametrize("description", [None, adf(""), adf("   "), adf("TBD")])
    def test_empty_requirement_exits_6_and_writes_no_draft(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any, description: Any
    ) -> None:
        """FR-004: the draft must not exist, not merely be empty."""
        self._issue_with(jira_mock, description)

        with pytest.raises(InsufficientContent) as exc:
            attempt(tmp_path, stub_generation(default_payload()))

        assert exc.value.exit_code == 6
        assert drafts_written(tmp_path) == []

    def test_sparse_requirement_is_not_sent_to_the_model(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        self._issue_with(jira_mock, adf("TBD"))
        stub = stub_generation(default_payload())
        with pytest.raises(InsufficientContent):
            attempt(tmp_path, stub)
        assert stub.calls == []

    def test_message_names_the_issue(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        self._issue_with(jira_mock, None, summary="Placeholder story")
        with pytest.raises(InsufficientContent) as exc:
            attempt(tmp_path, stub_generation(default_payload()))
        assert "PROJ-123" in exc.value.message
        assert "Placeholder story" in exc.value.message


class TestPermissionGate:
    @pytest.mark.parametrize(
        "granted",
        [
            {"CREATE_ISSUES": False, "LINK_ISSUES": True},
            {"CREATE_ISSUES": True, "LINK_ISSUES": False},
            {"CREATE_ISSUES": False, "LINK_ISSUES": False},
        ],
    )
    def test_missing_permission_exits_7_before_generation(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any, granted: dict[str, bool]
    ) -> None:
        """FR-022: stop before creating anything -- and before paying for a model call."""
        jira_mock.get("/mypermissions").mock(
            return_value=httpx.Response(
                200,
                json={"permissions": {k: {"havePermission": v} for k, v in granted.items()}},
            )
        )
        stub = stub_generation(default_payload())

        with pytest.raises(MissingProjectPermission) as exc:
            attempt(tmp_path, stub)

        assert exc.value.exit_code == 7
        assert "PROJ" in exc.value.message
        assert stub.calls == []
        assert drafts_written(tmp_path) == []

    def test_link_permission_alone_is_a_hard_stop(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """An account that can create but not link would produce orphan test cases."""
        jira_mock.get("/mypermissions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "permissions": {
                        "CREATE_ISSUES": {"havePermission": True},
                        "LINK_ISSUES": {"havePermission": False},
                    }
                },
            )
        )
        with pytest.raises(MissingProjectPermission) as exc:
            attempt(tmp_path, stub_generation(default_payload()))
        assert "LINK_ISSUES" in exc.value.message
        assert "link each test case" in exc.value.remediation


class TestSiteConfig:
    def test_unknown_link_type_exits_13_and_lists_alternatives(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        with pytest.raises(SiteConfigUnresolved) as exc:
            attempt(tmp_path, stub_generation(default_payload()), link_type="Verifies")

        assert exc.value.exit_code == 13
        assert "Relates" in exc.value.remediation
        assert "Blocks" in exc.value.remediation

    def test_unknown_issue_type_lists_what_the_project_has(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        with pytest.raises(SiteConfigUnresolved) as exc:
            attempt(tmp_path, stub_generation(default_payload()), issue_type="TestCase")
        assert "Task" in exc.value.remediation
        assert "Bug" in exc.value.remediation

    def test_unknown_ac_field_is_an_error_not_a_silent_fallback(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """Ignoring it would make the field look empty rather than misconfigured."""
        with pytest.raises(SiteConfigUnresolved) as exc:
            attempt(tmp_path, stub_generation(default_payload()), ac_field="Criteria")

        assert exc.value.exit_code == 13
        assert "Acceptance Criteria" in exc.value.remediation

    def test_site_config_failure_costs_no_model_call(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        stub = stub_generation(default_payload())
        with pytest.raises(SiteConfigUnresolved):
            attempt(tmp_path, stub, link_type="Verifies")
        assert stub.calls == []


class TestGenerationFailures:
    def test_service_error_exits_8_naming_the_generation_service(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """FR-031: distinguishable from a Jira problem."""
        failure = GenerationFailure(
            "Could not reach the generation service.", "Check your network connection."
        )
        with pytest.raises(GenerationFailure) as exc:
            attempt(tmp_path, stub_generation(failure))

        assert exc.value.exit_code == 8
        assert "generation service" in exc.value.message

    def test_unusable_output_writes_no_partial_draft(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """A draft the tool knows is broken must never reach review."""
        with pytest.raises(GenerationFailure):
            attempt(tmp_path, stub_generation({"test_cases": [], "coverage_notes": []}))
        assert drafts_written(tmp_path) == []

    def test_invalid_json_is_reported_clearly(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        with pytest.raises(GenerationFailure) as exc:
            attempt(tmp_path, stub_generation("{not valid json"))
        assert "not valid JSON" in exc.value.message
        assert drafts_written(tmp_path) == []

    def test_empty_response_is_reported(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        with pytest.raises(GenerationFailure) as exc:
            attempt(tmp_path, stub_generation("   "))
        assert "empty response" in exc.value.message

    def test_over_cap_output_is_rejected_not_truncated(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        from .conftest import make_case

        payload = {"test_cases": [make_case(i) for i in range(1, 8)], "coverage_notes": []}
        with pytest.raises(GenerationFailure) as exc:
            attempt(tmp_path, stub_generation(payload), max_cases=5)

        assert "7 test cases, over the limit of 5" in exc.value.message
        assert drafts_written(tmp_path) == []

    def test_malformed_case_names_its_position(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        from .conftest import make_case

        bad = make_case(2, expected_results=["only one", "but two"], steps=["a", "b", "c"])
        payload = {"test_cases": [make_case(1), bad], "coverage_notes": []}

        with pytest.raises(GenerationFailure) as exc:
            attempt(tmp_path, stub_generation(payload))

        assert "Test case 2" in exc.value.message


class TestNoCredentialLeakage:
    def test_failure_messages_never_contain_secrets(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any, jira_env: dict[str, str]
    ) -> None:
        """FR-027, exercised on an error path rather than the happy path."""
        jira_mock.get(path__regex=r"/issue/[A-Z][A-Z0-9_]+-\d+$").mock(
            return_value=httpx.Response(401)
        )
        with pytest.raises(AuthFailure) as exc:
            attempt(tmp_path, stub_generation(default_payload()))

        rendered = exc.value.render()
        assert jira_env["JIRA_API_TOKEN"] not in rendered
        assert jira_env["ANTHROPIC_API_KEY"] not in rendered


class TestAnonymousResponsesAreTreatedAsAuthFailures:
    """Found by T073, running against a real Atlassian Cloud site.

    Most Jira read endpoints do not return 401 for a bad token -- they serve the request
    anonymously and return 200 with an empty result. Because site configuration is resolved
    before the issue is fetched (contracts/cli.md step 2), the tool's first call was
    `GET /issueLinkType`, which came back 200 with zero link types, and the user was told:

        This Jira site has no issue link type named 'Relates'.

    Exit 13 -- a configuration problem -- when their token was simply wrong. contracts/cli.md
    is explicit that exit 5 must stay distinguishable from a configuration failure, and this
    is the regression test for that.
    """

    def test_an_anonymous_myself_response_exits_5_not_13(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        # What Atlassian Cloud actually returns for a bad token on this endpoint.
        jira_mock.get("/myself").mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(AuthFailure) as exc:
            attempt(tmp_path, stub_generation(default_payload(2)))
        assert exc.value.exit_code == 5

    def test_a_401_on_myself_exits_5(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        jira_mock.get("/myself").mock(return_value=httpx.Response(401))
        with pytest.raises(AuthFailure) as exc:
            attempt(tmp_path, stub_generation(default_payload(2)))
        assert exc.value.exit_code == 5

    def test_the_message_explains_the_anonymous_behaviour(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """SC-008: the user needs to know to look at their token, not their link types."""
        jira_mock.get("/myself").mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(AuthFailure) as exc:
            attempt(tmp_path, stub_generation(default_payload(2)))
        rendered = exc.value.render()
        assert "JIRA_API_TOKEN" in rendered
        assert "anonymous" in rendered.lower()

    def test_auth_is_checked_before_site_configuration(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """Order matters: a bad token must not be reported as a missing link type, which
        means the probe has to run before /issueLinkType is interpreted."""
        jira_mock.get("/myself").mock(return_value=httpx.Response(200, json={}))
        jira_mock.get("/issueLinkType").mock(
            return_value=httpx.Response(200, json={"issueLinkTypes": []})
        )
        with pytest.raises(AuthFailure):
            attempt(tmp_path, stub_generation(default_payload(2)))

    def test_a_valid_account_passes_the_probe(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """The probe must not become a new way for a good run to fail."""
        import typer

        with pytest.raises(typer.Exit) as exc:
            attempt(tmp_path, stub_generation(default_payload(2)))
        assert exc.value.exit_code == 0
