"""T070: `--json` emits one parseable object on stdout (FR-028, quickstart Scenario 10).

The assertion that matters is ``json.loads(stdout)`` on the *whole* stream, not a search
for a JSON-looking substring. A rich table, a progress line or a stray `print` mixed into
stdout breaks every scripted caller, and it breaks them in the most annoying way possible
-- intermittently, depending on which path the run happened to take.

Diagnostics are allowed on stderr. That is the point of having two streams.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import typer

from jira_testgen.commands.approve import run_approve
from jira_testgen.commands.generate import run_generate

from .conftest import default_payload, make_row

pytestmark = pytest.mark.integration


def parse_stdout(capsys: Any) -> dict[str, Any]:
    """Parse the entire stdout stream, so anything decorative fails the test."""
    captured = capsys.readouterr()
    assert captured.out.strip(), "nothing was written to stdout"
    try:
        payload = json.loads(captured.out)
    except json.JSONDecodeError as exc:  # pragma: no cover - the failure message is the point
        pytest.fail(f"stdout is not one JSON object ({exc}):\n{captured.out!r}")
    assert isinstance(payload, dict), "stdout must hold one object, not a list or scalar"
    return payload


def generate(tmp_path: Path, stub_generation: Any, **kwargs: Any) -> int:
    options: dict[str, Any] = {
        "issue_key": "PROJ-123",
        "target_project": None,
        "ac_field": None,
        "issue_type": None,
        "link_type": None,
        "max_cases": 25,
        "workspace": tmp_path / ".jira-testgen",
        "no_wait": False,
        "json_output": True,
        "yes": False,
        "generation_client": stub_generation(default_payload(4)),
    }
    options.update(kwargs)
    with pytest.raises(typer.Exit) as exc:
        run_generate(**options)
    return int(exc.value.exit_code)


def approve(run: Any, **kwargs: Any) -> int:
    options: dict[str, Any] = {
        "run_id": run.run_id,
        "reject": False,
        "only": None,
        "dry_run": False,
        "workspace": run.workspace,
        "json_output": True,
    }
    # The CLI spells it --yes; the function spells it approve_all.
    if "yes" in kwargs:
        options["approve_all"] = kwargs.pop("yes")
    options.update(kwargs)
    with pytest.raises(typer.Exit) as exc:
        run_approve(**options)
    return int(exc.value.exit_code)


class TestGenerateJson:
    def test_stdout_is_one_object(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any, capsys: Any
    ) -> None:
        assert generate(tmp_path, stub_generation, no_wait=True) == 0
        payload = parse_stdout(capsys)
        assert payload["issue_key"] == "PROJ-123"
        assert payload["cases"] == 4

    def test_the_draft_table_is_suppressed(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any, capsys: Any
    ) -> None:
        """The rich table is the thing most likely to leak into stdout."""
        generate(tmp_path, stub_generation, no_wait=True)
        out = capsys.readouterr().out
        assert "┌" not in out and "Draft for" not in out

    def test_it_carries_the_run_id_a_script_needs_next(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any, capsys: Any
    ) -> None:
        """Scenario 10 pipes this straight into `approve`."""
        generate(tmp_path, stub_generation, no_wait=True)
        payload = parse_stdout(capsys)
        assert payload["run_id"]
        assert payload["draft_path"]

    def test_without_yes_it_says_it_is_still_awaiting_review(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any, capsys: Any
    ) -> None:
        """--json cannot show a prompt, so it stops at the draft. A script must be able
        to tell that apart from a completed publish."""
        assert generate(tmp_path, stub_generation) == 0
        payload = parse_stdout(capsys)
        assert payload["awaiting_review"] is True
        assert payload["published"] is False
        assert "approve" in payload["next_command"]

    def test_yes_is_not_cancelled_by_json(
        self, tmp_path: Path, jira_writes: Any, stub_generation: Any, capsys: Any
    ) -> None:
        """--yes is an explicit instruction to publish. --json must not quietly
        countermand it -- a script asking for both wants issues created."""
        assert generate(tmp_path, stub_generation, yes=True) == 0
        payload = parse_stdout(capsys)
        assert payload["phase"] == "published"
        assert len(payload["created"]) == 4
        assert len(jira_writes.created) == 4

    def test_no_wait_and_yes_together_are_refused(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any
    ) -> None:
        from jira_testgen.errors import InvalidArguments

        with pytest.raises(InvalidArguments) as exc:
            generate(tmp_path, stub_generation, no_wait=True, yes=True)
        assert exc.value.exit_code == 2


class TestApproveJson:
    def test_publish_result_is_one_object(
        self, make_run: Any, jira_writes: Any, capsys: Any
    ) -> None:
        run = make_run([make_row(i) for i in range(1, 4)])
        assert approve(run) == 0
        payload = parse_stdout(capsys)
        assert payload["phase"] == "published"
        assert len(payload["created"]) == 3
        assert payload["failures"] == []

    def test_the_created_table_is_suppressed(
        self, make_run: Any, jira_writes: Any, capsys: Any
    ) -> None:
        run = make_run([make_row(1)])
        approve(run)
        assert "┌" not in capsys.readouterr().out

    def test_dry_run_is_one_object(self, make_run: Any, jira_writes: Any, capsys: Any) -> None:
        run = make_run([make_row(1)])
        assert approve(run, dry_run=True) == 0
        payload = parse_stdout(capsys)
        assert payload["dry_run"] is True

    def test_rejection_is_one_object(self, make_run: Any, jira_writes: Any, capsys: Any) -> None:
        run = make_run([make_row(1)])
        assert approve(run, reject=True) == 0
        assert parse_stdout(capsys)["phase"] == "rejected"

    def test_nothing_to_publish_is_one_object(
        self, make_run: Any, jira_writes: Any, capsys: Any
    ) -> None:
        run = make_run([make_row(1, approval="pending")])
        assert approve(run) == 0
        payload = parse_stdout(capsys)
        assert payload["created"] == []
        assert payload["message"] == "Nothing to publish."

    def test_validation_warnings_do_not_pollute_stdout(
        self, make_run: Any, jira_writes: Any, capsys: Any
    ) -> None:
        """A V8 warning prints in human mode. In JSON mode it must not break the parse."""
        run = make_run([make_row(1, traces_to="AC-99")])
        assert approve(run) == 0
        parse_stdout(capsys)

    def test_yes_publishes_pending_rows(self, make_run: Any, jira_writes: Any, capsys: Any) -> None:
        """quickstart Scenario 10 drives `approve --yes --json` against a draft nobody
        edited, so every row is still `pending`."""
        run = make_run([make_row(i, approval="pending") for i in range(1, 4)])
        assert approve(run, yes=True) == 0
        payload = parse_stdout(capsys)
        assert len(payload["created"]) == 3

    def test_yes_still_honours_a_rejected_row(
        self, make_run: Any, jira_writes: Any, capsys: Any
    ) -> None:
        run = make_run([make_row(1, approval="pending"), make_row(2, approval="rejected")])
        approve(run, yes=True)
        assert len(parse_stdout(capsys)["created"]) == 1


class TestDraftsJson:
    def test_empty_listing_is_one_object(self, tmp_path: Path, capsys: Any) -> None:
        from typer.testing import CliRunner

        from jira_testgen.cli import app

        result = CliRunner().invoke(
            app, ["drafts", "--workspace", str(tmp_path / "empty"), "--json"]
        )
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"runs": []}

    def test_populated_listing_is_one_object(self, make_run: Any) -> None:
        from typer.testing import CliRunner

        from jira_testgen.cli import app

        run = make_run([make_row(1), make_row(2)])
        result = CliRunner().invoke(app, ["drafts", "--workspace", str(run.workspace), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert [r["run_id"] for r in payload["runs"]] == [run.run_id]
        assert payload["runs"][0]["cases"] == 2

    def test_the_table_is_suppressed(self, make_run: Any) -> None:
        from typer.testing import CliRunner

        from jira_testgen.cli import app

        run = make_run([make_row(1)])
        result = CliRunner().invoke(app, ["drafts", "--workspace", str(run.workspace), "--json"])
        assert "┌" not in result.stdout
        assert "awaiting review" not in result.stdout


class TestErrorsStayOffStdout:
    def test_a_validation_failure_writes_nothing_to_stdout(
        self, make_run: Any, jira_writes: Any, capsys: Any
    ) -> None:
        """A script reading stdout must get either one object or nothing -- never an
        error message it then tries to parse."""
        from jira_testgen.errors import DraftValidationFailure

        run = make_run([make_row(1, kind="smoke")])
        with pytest.raises(DraftValidationFailure):
            approve(run)
        assert capsys.readouterr().out.strip() == ""


class TestRunLog:
    """T071: a structured, redacted log in the run directory.

    The publish phase is the one most worth a record. A partial publish leaves the user
    asking which cases reached Jira and why the rest did not, and after the process has
    exited the terminal scrollback is all they would otherwise have.
    """

    def test_approve_writes_a_run_log(self, make_run: Any, jira_writes: Any) -> None:
        run = make_run([make_row(1)])
        approve(run)
        assert (run.directory / "run.log").exists()

    def test_every_line_is_a_json_object(self, make_run: Any, jira_writes: Any) -> None:
        run = make_run([make_row(i) for i in range(1, 3)])
        approve(run)
        lines = (run.directory / "run.log").read_text(encoding="utf-8").splitlines()
        assert lines
        for line in lines:
            record = json.loads(line)
            assert {"ts", "level", "logger", "message"} <= set(record)

    def test_it_records_which_issue_each_case_became(self, make_run: Any, jira_writes: Any) -> None:
        """The question a user actually asks after a partial publish."""
        run = make_run([make_row(i) for i in range(1, 3)])
        approve(run)
        content = (run.directory / "run.log").read_text(encoding="utf-8")
        assert "TC-AB12-001" in content
        assert jira_writes.created_keys[0] in content

    def test_it_records_a_failure(
        self, make_run: Any, jira_writes: Any, no_backoff: list[float]
    ) -> None:
        import httpx

        from jira_testgen.errors import PublishFailure

        run = make_run([make_row(1)])

        def drop(body: dict[str, Any], recorder: Any) -> Any:
            raise httpx.ConnectError("connection reset after send")

        jira_writes.on_create = drop
        with pytest.raises(PublishFailure):
            approve(run)
        content = (run.directory / "run.log").read_text(encoding="utf-8")
        assert "Ambiguous write" in content or "ambiguous" in content.lower()

    def test_it_contains_no_credential(
        self, make_run: Any, jira_writes: Any, jira_env: dict[str, str]
    ) -> None:
        """FR-027. The run log is the file a user attaches to a bug report."""
        run = make_run([make_row(1)])
        approve(run)
        content = (run.directory / "run.log").read_text(encoding="utf-8")
        assert jira_env["JIRA_API_TOKEN"] not in content
        assert jira_env["ANTHROPIC_API_KEY"] not in content

    def test_records_are_not_duplicated_across_the_handoff(
        self, tmp_path: Path, jira_writes: Any, stub_generation: Any
    ) -> None:
        """`generate` configures logging, then hands off to the publish sequence, which
        configures it again for the same run. Without the idempotence guard every record
        after the handoff would be written twice."""
        generate(tmp_path, stub_generation, yes=True)
        run_directory = next((tmp_path / ".jira-testgen" / "runs").iterdir())
        lines = (run_directory / "run.log").read_text(encoding="utf-8").splitlines()
        messages = [json.loads(line)["message"] for line in lines]
        assert len(messages) == len(set(messages)), "a record was logged twice"
