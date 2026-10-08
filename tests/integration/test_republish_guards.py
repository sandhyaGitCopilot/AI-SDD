"""T056: re-approving a published draft creates nothing (FR-023, research R4).

The second test in this file is the one that matters. Identity comes from ``test_id`` in
``state.json``, not from the summary text -- so a reviewer who notices a typo in an
already-published case, fixes it in the CSV, and re-approves must not get a second issue.

A guard built on summary matching would pass every other test here and fail that one, and
it would fail it silently: the user would see two issues and have no way to tell which the
tool thought was which.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import typer

from jira_testgen.commands.approve import run_approve
from jira_testgen.draft.state import RunStateStore
from jira_testgen.models import RunPhase

from .conftest import make_row, render_csv

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


class TestReapprovingAFullyPublishedDraft:
    def test_it_creates_nothing(self, make_run: Any, jira_writes: Any) -> None:
        run = make_run([make_row(i) for i in range(1, 4)])
        approve(run)
        assert len(jira_writes.created) == 3

        approve(run)
        assert len(jira_writes.created) == 3

    def test_it_exits_0(self, make_run: Any, jira_writes: Any) -> None:
        """Nothing left to do is a success, not a failure."""
        run = make_run([make_row(1)])
        approve(run)
        assert approve(run) == 0

    def test_it_says_already_published(self, make_run: Any, jira_writes: Any, capsys: Any) -> None:
        run = make_run([make_row(1)])
        approve(run)
        capsys.readouterr()
        approve(run)
        assert "already published" in capsys.readouterr().out.lower()

    def test_it_issues_no_write_calls_at_all(self, make_run: Any, jira_writes: Any) -> None:
        run = make_run([make_row(i) for i in range(1, 4)])
        approve(run)
        before = (jira_writes.create_attempts, jira_writes.link_attempts)
        approve(run)
        assert (jira_writes.create_attempts, jira_writes.link_attempts) == before

    def test_the_json_report_names_the_skipped_cases(
        self, make_run: Any, jira_writes: Any, capsys: Any
    ) -> None:
        run = make_run([make_row(1), make_row(2)])
        approve(run)
        capsys.readouterr()
        approve(run, json_output=True)
        payload = json.loads(capsys.readouterr().out)
        assert payload["created"] == []
        assert sorted(payload["already_published"]) == ["TC-AB12-001", "TC-AB12-002"]

    def test_the_phase_stays_published(self, make_run: Any, jira_writes: Any) -> None:
        run = make_run([make_row(1)])
        approve(run)
        approve(run)
        assert RunStateStore(run.directory).load().phase is RunPhase.PUBLISHED


class TestIdentityComesFromTheIdentifierNotTheSummary:
    def test_editing_a_published_summary_does_not_create_a_second_issue(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        """research R4, stated as a user story: the reviewer spots a typo after
        publishing, fixes it, and re-approves."""
        run = make_run([make_row(1, summary="Original summary with a typpo")])
        approve(run)
        assert jira_writes.created_summaries == ["Original summary with a typpo"]

        run.draft_path.write_bytes(
            render_csv([make_row(1, summary="Original summary with the typo fixed")])
        )
        approve(run)
        assert len(jira_writes.created) == 1

    def test_the_jira_issue_keeps_the_text_it_was_filed_with(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        """An honest limitation, asserted so it is a known property rather than a
        surprise: editing the CSV after publishing does not update Jira."""
        run = make_run([make_row(1, summary="As filed")])
        approve(run)
        run.draft_path.write_bytes(render_csv([make_row(1, summary="Edited after filing")]))
        approve(run)
        assert jira_writes.created_summaries == ["As filed"]

    def test_a_changed_identifier_is_treated_as_a_new_case(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        """The flip side, and why the preamble says not to edit the column (FR-006a).
        Changing the identifier is indistinguishable from adding a case."""
        run = make_run([make_row(1)])
        approve(run)

        run.draft_path.write_bytes(render_csv([make_row(1, test_id="TC-AB12-099")]))
        approve(run)
        assert len(jira_writes.created) == 2

    def test_a_row_added_after_publishing_is_published(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        run = make_run([make_row(1)])
        approve(run)

        run.draft_path.write_bytes(render_csv([make_row(1), make_row(2)]))
        approve(run)
        assert len(jira_writes.created) == 2
        assert jira_writes.created_summaries[1] == "Verify password reset behaviour 2"

    def test_a_blank_identifier_added_after_publishing_is_published_once(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        """The user-added row gets an identifier, it is written back, and a third
        approve must not file it again."""
        run = make_run([make_row(1)])
        approve(run)

        run.draft_path.write_bytes(
            render_csv([make_row(1), make_row(2, test_id="", summary="A case I typed")])
        )
        approve(run)
        assert len(jira_writes.created) == 2

        approve(run)
        assert len(jira_writes.created) == 2


class TestTheGuardIgnoresTheCsvJiraKeyColumn:
    def test_clearing_jira_key_does_not_cause_a_duplicate(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        """contracts/draft-csv.md: jira_key is informational. The guard reads state.json,
        and a user who clears, sorts or copies that column must not be able to break it."""
        from jira_testgen.draft.csv_io import read_draft_rows

        run = make_run([make_row(1), make_row(2)])
        approve(run)
        assert all(row["jira_key"] for row in read_draft_rows(run.draft_path))

        rows = read_draft_rows(run.draft_path)
        for row in rows:
            row["jira_key"] = ""
        run.draft_path.write_bytes(render_csv(rows))

        approve(run)
        assert len(jira_writes.created) == 2

    def test_a_fabricated_jira_key_does_not_cause_a_skip(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        """The other direction: inventing a key in the CSV must not make the tool think
        a case is already filed and silently skip it."""
        run = make_run([make_row(1, jira_key="QA-99999")])
        approve(run)
        assert len(jira_writes.created) == 1
