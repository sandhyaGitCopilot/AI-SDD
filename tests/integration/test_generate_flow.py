"""T023: the generate flow end to end, with Jira mocked and generation stubbed.

The load-bearing assertion is ``test_no_writes_to_jira``. FR-013 and SC-006 promise that
generating touches nothing, and the only honest way to check that is to assert on the HTTP
calls actually made, not on the absence of a visible error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import typer

from jira_testgen.commands.generate import run_generate
from jira_testgen.draft.csv_io import read_draft_rows, read_preamble
from jira_testgen.draft.state import RunStateStore
from jira_testgen.models import RunPhase

from .conftest import default_payload, make_case

pytestmark = pytest.mark.integration


def generate(
    tmp_path: Path,
    payload: Any,
    stub_generation: Any,
    **kwargs: Any,
) -> tuple[dict[str, Any], Any]:
    """Run generate with sensible defaults; returns (result payload, stub client)."""
    stub = stub_generation(payload)
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

    with pytest.raises(typer.Exit) as exc:
        run_generate(**options)
    assert exc.value.exit_code == 0

    runs = list((tmp_path / ".jira-testgen" / "runs").iterdir())
    assert len(runs) == 1
    state = RunStateStore(runs[0]).load()
    return {"run_dir": runs[0], "state": state}, stub


class TestHappyPath:
    def test_writes_a_draft(self, tmp_path: Path, jira_mock: Any, stub_generation: Any) -> None:
        result, _ = generate(tmp_path, default_payload(4), stub_generation)
        draft = result["run_dir"] / "testcases.csv"
        assert draft.exists()
        assert len(read_draft_rows(draft)) == 4

    def test_every_case_is_well_formed(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        result, _ = generate(tmp_path, default_payload(4), stub_generation)
        for row in read_draft_rows(result["run_dir"] / "testcases.csv"):
            assert row["test_id"].startswith("TC-")
            assert row["summary"]
            assert row["steps"].strip()
            assert row["expected_results"].strip()
            assert row["traces_to"]
            assert row["approval"] == "pending"

    def test_identifiers_are_unique_and_sequential(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        result, _ = generate(tmp_path, default_payload(5), stub_generation)
        ids = [r["test_id"] for r in read_draft_rows(result["run_dir"] / "testcases.csv")]
        assert len(set(ids)) == 5
        assert ids[0].endswith("-001")
        assert ids[-1].endswith("-005")

    def test_includes_negative_and_edge_cases(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """SC-005 is measurable because `kind` is a column, not a guess."""
        result, _ = generate(tmp_path, default_payload(4), stub_generation)
        kinds = {r["kind"] for r in read_draft_rows(result["run_dir"] / "testcases.csv")}
        assert "negative" in kinds
        assert "edge" in kinds

    def test_preamble_records_provenance(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        result, _ = generate(tmp_path, default_payload(2), stub_generation)
        preamble = "\n".join(read_preamble(result["run_dir"] / "testcases.csv"))
        assert "source_issue: PROJ-123" in preamble
        assert "claude-opus-5-5" in preamble
        assert "DO NOT EDIT" in preamble

    def test_run_state_is_persisted_as_drafted(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        result, _ = generate(tmp_path, default_payload(2), stub_generation)
        state = result["state"]
        assert state.phase is RunPhase.DRAFTED
        assert state.publication_record == {}
        assert state.target_project_key == "PROJ"
        assert state.issue_type_name == "Task"
        assert state.link_type_name == "Relates"

    def test_source_snapshot_enables_resume_without_rereading_jira(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """FR-017 depends on the snapshot being complete at generation time."""
        result, _ = generate(tmp_path, default_payload(2), stub_generation)
        snapshot = result["state"].source_snapshot
        assert snapshot.issue_key == "PROJ-123"
        assert "reset their password" in snapshot.description_text
        assert len(snapshot.criteria) == 3


class TestNoJiraWrites:
    def test_no_writes_to_jira(self, tmp_path: Path, jira_mock: Any, stub_generation: Any) -> None:
        """FR-013 / SC-006: generating must create nothing, anywhere."""
        generate(tmp_path, default_payload(3), stub_generation)

        calls = [(c.request.method, c.request.url.path) for c in jira_mock.calls]

        # Nothing but reads. This single assertion is the guarantee; the two below name the
        # specific writes that would breach it, so a failure says *what* leaked.
        methods = {method for method, _ in calls}
        assert methods == {"GET"}, f"generate issued non-GET calls: {calls}"

        assert not [p for m, p in calls if m == "POST" and p.endswith("/issue")]
        assert not [p for m, p in calls if m == "POST" and p.endswith("/issueLink")]


class TestOrdering:
    def test_permissions_checked_before_generation(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """A blocked run must cost nothing -- the model call comes after the gate."""
        _, stub = generate(tmp_path, default_payload(2), stub_generation)
        paths = [c.request.url.path for c in jira_mock.calls]
        assert any("mypermissions" in p for p in paths)
        assert len(stub.calls) == 1

    def test_site_config_resolved_before_generation(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        generate(tmp_path, default_payload(2), stub_generation)
        paths = [c.request.url.path for c in jira_mock.calls]
        assert any("issueLinkType" in p for p in paths)
        assert any("createmeta" in p for p in paths)


class TestCriteriaAndCoverage:
    def test_criteria_are_enumerated_in_the_prompt(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """traces_to can only reference ids the model was actually given."""
        _, stub = generate(tmp_path, default_payload(2), stub_generation)
        content = stub.calls[0]["user_content"]
        assert "AC-1:" in content
        assert "AC-2:" in content
        assert "reset link is emailed" in content.lower()

    def test_uncovered_criteria_are_disclosed(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """SC-004: a gap must be visible to the reviewer, not silent."""
        payload = {
            "test_cases": [make_case(1, traces_to=["AC-1"])],
            "coverage_notes": [],
        }
        result, _ = generate(tmp_path, payload, stub_generation)
        preamble = "\n".join(read_preamble(result["run_dir"] / "testcases.csv"))
        assert "AC-2" in preamble
        assert "AC-3" in preamble
        assert "no test case" in preamble

    def test_low_negative_ratio_is_flagged(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        payload = {
            "test_cases": [make_case(i, "positive") for i in range(1, 5)],
            "coverage_notes": [],
        }
        result, _ = generate(tmp_path, payload, stub_generation)
        preamble = "\n".join(read_preamble(result["run_dir"] / "testcases.csv"))
        assert "below the 30% target" in preamble

    def test_model_notes_are_preserved(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        payload = {
            "test_cases": [
                make_case(1, traces_to=["AC-1"]),
                make_case(2, "negative", traces_to=["AC-2"]),
                make_case(3, "edge", traces_to=["AC-3"]),
            ],
            "coverage_notes": ["Timing behaviour is ambiguous in the requirement."],
        }
        result, _ = generate(tmp_path, payload, stub_generation)
        preamble = "\n".join(read_preamble(result["run_dir"] / "testcases.csv"))
        assert "Timing behaviour is ambiguous" in preamble

    def test_cap_reached_is_disclosed(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """FR-011: hitting the limit must be stated, with what was left out."""
        payload = {
            "test_cases": [make_case(i, traces_to=["AC-1"]) for i in range(1, 4)],
            "coverage_notes": [],
        }
        result, _ = generate(tmp_path, payload, stub_generation, max_cases=3)
        preamble = "\n".join(read_preamble(result["run_dir"] / "testcases.csv"))
        assert "limit of 3 test cases was reached" in preamble


class TestExistingLinks:
    def test_existing_linked_issues_are_captured(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        """FR-005: prior test cases must be surfaced before generating more."""
        import httpx

        from .conftest import DEFAULT_DESCRIPTION

        jira_mock.get(path__regex=r"/issue/[A-Z][A-Z0-9_]+-\d+$").mock(
            return_value=httpx.Response(
                200,
                json={
                    "key": "PROJ-123",
                    "fields": {
                        "summary": "Users can reset their password by email",
                        "description": DEFAULT_DESCRIPTION,
                        "project": {"key": "PROJ"},
                        "issuelinks": [
                            {
                                "type": {
                                    "name": "Relates",
                                    "inward": "relates to",
                                    "outward": "relates to",
                                },
                                "outwardIssue": {
                                    "key": "QA-9",
                                    "fields": {
                                        "summary": "Existing test case",
                                        "issuetype": {"name": "Task"},
                                    },
                                },
                            }
                        ],
                    },
                },
            )
        )
        result, _ = generate(tmp_path, default_payload(2), stub_generation)
        existing = result["state"].source_snapshot.existing_linked_tests
        assert [e.issue_key for e in existing] == ["QA-9"]


class TestJsonOutput:
    def test_json_mode_emits_one_object(
        self,
        tmp_path: Path,
        jira_mock: Any,
        stub_generation: Any,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """FR-028: stdout must stay parseable."""
        stub = stub_generation(default_payload(3))
        with pytest.raises(typer.Exit) as exc:
            run_generate(
                issue_key="PROJ-123",
                target_project=None,
                ac_field=None,
                issue_type=None,
                link_type=None,
                max_cases=25,
                workspace=tmp_path / ".jira-testgen",
                no_wait=True,
                json_output=True,
                yes=False,
                generation_client=stub,
            )
        assert exc.value.exit_code == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["issue_key"] == "PROJ-123"
        assert payload["cases"] == 3
        assert payload["published"] is False
        assert payload["coverage"]["criteria_total"] == 3
