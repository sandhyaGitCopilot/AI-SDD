"""T054: approved cases become linked Jira issues (FR-019, FR-020, FR-024).

The happy path, asserted against the HTTP calls actually made rather than against the
tool's own report of what it did. A publisher that recorded success without issuing the
requests would satisfy every state assertion in this file and none of the call assertions.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import typer

from jira_testgen.commands.approve import run_approve
from jira_testgen.draft.state import RunStateStore
from jira_testgen.models import RunPhase

from .conftest import make_row

pytestmark = pytest.mark.integration


def approve(run: Any, **kwargs: Any) -> int:
    options: dict[str, Any] = {
        "run_id": run.run_id,
        "reject": False,
        "only": None,
        "dry_run": False,
        "workspace": run.workspace,
        "json_output": False,
    }
    options.update(kwargs)
    with pytest.raises(typer.Exit) as exc:
        run_approve(**options)
    return int(exc.value.exit_code)


class TestNApprovedCasesProduceNIssues:
    def test_every_approved_case_is_created(self, make_run: Any, jira_writes: Any) -> None:
        run = make_run([make_row(i) for i in range(1, 4)])
        assert approve(run) == 0
        assert len(jira_writes.created) == 3

    def test_every_created_issue_is_linked_to_the_source(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        """FR-020. An unlinked test case satisfies nothing -- it is a stray issue."""
        run = make_run([make_row(i) for i in range(1, 4)])
        approve(run)
        assert sorted(jira_writes.linked_keys) == sorted(jira_writes.created_keys)
        assert {item["outward"] for item in jira_writes.links} == {"PROJ-123"}

    def test_the_link_uses_the_resolved_link_type(self, make_run: Any, jira_writes: Any) -> None:
        run = make_run([make_row(1)])
        approve(run)
        assert jira_writes.links[0]["type"] == "Relates"

    def test_rejected_and_pending_cases_are_not_published(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        run = make_run(
            [
                make_row(1, approval="approved"),
                make_row(2, approval="rejected"),
                make_row(3, approval="pending"),
            ]
        )
        approve(run)
        assert jira_writes.created_summaries == ["Verify password reset behaviour 1"]


class TestTheCreatedIssue:
    def test_it_goes_to_the_target_project_with_the_resolved_issue_type(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        run = make_run([make_row(1)])
        approve(run)
        fields = jira_writes.created[0]["body"]["fields"]
        assert fields["project"]["key"] == "PROJ"
        assert fields["issuetype"]["id"] == "10003"

    def test_the_description_is_an_adf_document_not_a_string(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        """research R1: Jira returns 400 for a plain string. This is the assertion that
        catches a regression to one."""
        run = make_run([make_row(1)])
        approve(run)
        description = jira_writes.created[0]["body"]["fields"]["description"]
        assert isinstance(description, dict)
        assert description["type"] == "doc"
        assert description["version"] == 1

    def test_the_description_carries_steps_and_expected_results(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        run = make_run([make_row(1)])
        approve(run)
        rendered = json.dumps(jira_writes.created[0]["body"]["fields"]["description"])
        assert "Steps" in rendered
        assert "Expected results" in rendered
        assert "Open the password reset page" in rendered

    def test_the_summary_is_the_reviewed_summary(self, make_run: Any, jira_writes: Any) -> None:
        run = make_run([make_row(1, summary="The exact text the reviewer approved")])
        approve(run)
        assert jira_writes.created_summaries == ["The exact text the reviewer approved"]

    def test_notes_are_never_published(self, make_run: Any, jira_writes: Any) -> None:
        """contracts/draft-csv.md: the notes column is for the reviewer, not for Jira."""
        run = make_run([make_row(1, notes="internal reviewer chatter, do not ship")])
        approve(run)
        assert "reviewer chatter" not in json.dumps(jira_writes.created[0]["body"])


class TestTheRunRecord:
    def test_every_case_is_recorded_with_its_issue_key(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        run = make_run([make_row(i) for i in range(1, 4)])
        approve(run)
        state = RunStateStore(run.directory).load()
        assert len(state.published_ids()) == 3

    def test_every_case_is_recorded_as_linked(self, make_run: Any, jira_writes: Any) -> None:
        run = make_run([make_row(i) for i in range(1, 4)])
        approve(run)
        state = RunStateStore(run.directory).load()
        assert state.entries_needing_link() == []

    def test_the_phase_becomes_published(self, make_run: Any, jira_writes: Any) -> None:
        run = make_run([make_row(1)])
        approve(run)
        assert RunStateStore(run.directory).load().phase is RunPhase.PUBLISHED


class TestTheReport:
    def test_it_lists_every_issue_key_with_a_url(
        self, make_run: Any, jira_writes: Any, capsys: Any
    ) -> None:
        """FR-024. A key with no URL makes the reviewer search for their own output."""
        run = make_run(
            [make_row(i) for i in range(1, 3)],
        )
        approve(run)
        out = capsys.readouterr().out
        for key in jira_writes.created_keys:
            assert key in out
        assert "https://example.atlassian.net/browse/" in out

    def test_json_carries_the_same_facts(
        self, make_run: Any, jira_writes: Any, capsys: Any
    ) -> None:
        run = make_run([make_row(i) for i in range(1, 3)])
        approve(run, json_output=True)
        payload = json.loads(capsys.readouterr().out)
        assert payload["phase"] == "published"
        assert [c["issue_key"] for c in payload["created"]] == jira_writes.created_keys
        assert all(c["issue_url"].endswith(c["issue_key"]) for c in payload["created"])
        assert payload["failures"] == []


class TestJiraKeyWriteBack:
    def test_the_draft_records_what_was_filed(self, make_run: Any, jira_writes: Any) -> None:
        """T065. Informational, so the reviewer can see the result without leaving the
        file they have been working in."""
        from jira_testgen.draft.csv_io import read_draft_rows

        run = make_run([make_row(i) for i in range(1, 3)])
        approve(run)
        keys = [row["jira_key"] for row in read_draft_rows(run.draft_path)]
        assert keys == jira_writes.created_keys

    def test_unpublished_rows_keep_an_empty_jira_key(self, make_run: Any, jira_writes: Any) -> None:
        from jira_testgen.draft.csv_io import read_draft_rows

        run = make_run([make_row(1), make_row(2, approval="rejected")])
        approve(run)
        rows = read_draft_rows(run.draft_path)
        assert rows[0]["jira_key"]
        assert rows[1]["jira_key"] == ""

    def test_nothing_else_in_the_draft_changes(self, make_run: Any, jira_writes: Any) -> None:
        from jira_testgen.draft.csv_io import read_draft_rows

        run = make_run([make_row(1, notes="keep me, with a comma")])
        before = {k: v for k, v in read_draft_rows(run.draft_path)[0].items()}
        approve(run)
        after = read_draft_rows(run.draft_path)[0]
        for column, value in before.items():
            if column != "jira_key":
                assert after[column] == value
