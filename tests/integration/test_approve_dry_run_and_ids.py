"""T046 and T052: the dry run, and identifiers written back for rows the user added.

The write-back is the part that matters most here, and it is easy to miss. A reviewer who
types a new row into the spreadsheet leaves ``test_id`` blank; validation assigns one
(FR-023a). If that identifier is never written back to the file, the *next* run assigns a
different one to the same row, the publication record keyed on the old one no longer
matches, and the case is published a second time -- the duplicate SC-007 forbids, arriving
by a route no amount of care in the writer would catch.

So these tests check the file on disk after approving, not just the return value.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import typer

from jira_testgen.commands.approve import run_approve
from jira_testgen.draft.csv_io import read_draft_rows
from jira_testgen.draft.state import RunStateStore
from jira_testgen.models import RunPhase

from .conftest import make_row

pytestmark = pytest.mark.integration


def approve(run: Any, publisher: Any, **kwargs: Any) -> int:
    options: dict[str, Any] = {
        "run_id": run.run_id,
        "reject": False,
        "only": None,
        "dry_run": False,
        "workspace": run.workspace,
        "json_output": False,
        "publisher": publisher,
    }
    options.update(kwargs)
    with pytest.raises(typer.Exit) as exc:
        run_approve(**options)
    return int(exc.value.exit_code)


# ---------------------------------------------------------------------------
# T052 -- --dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_it_exits_0(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run([make_row(i) for i in range(1, 4)])
        assert approve(run, stub_publisher(), dry_run=True) == 0

    def test_it_touches_nothing_in_jira(
        self, make_run: Any, stub_publisher: Any, jira_mock: Any
    ) -> None:
        run = make_run([make_row(i) for i in range(1, 4)])
        publisher = stub_publisher()
        approve(run, publisher, dry_run=True)
        assert publisher.calls == 0
        assert list(jira_mock.calls) == []

    def test_it_does_not_advance_the_phase(self, make_run: Any, stub_publisher: Any) -> None:
        """A dry run must leave the draft exactly as approvable as it was."""
        run = make_run([make_row(1)])
        approve(run, stub_publisher(), dry_run=True)
        assert RunStateStore(run.directory).load().phase is RunPhase.DRAFTED

    def test_it_reports_what_would_be_created(
        self, make_run: Any, stub_publisher: Any, capsys: Any
    ) -> None:
        run = make_run([make_row(1, summary="A distinctive summary to look for")])
        approve(run, stub_publisher(), dry_run=True)
        out = capsys.readouterr().out
        assert "TC-AB12-001" in out
        assert "would be created" in out

    def test_json_lists_what_would_be_created(
        self, make_run: Any, stub_publisher: Any, capsys: Any
    ) -> None:
        run = make_run([make_row(1), make_row(2, approval="rejected")])
        approve(run, stub_publisher(), dry_run=True, json_output=True)
        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True
        assert [c["test_id"] for c in payload["would_create"]] == ["TC-AB12-001"]
        assert payload["created"] == []

    def test_it_reports_cases_that_would_be_skipped_as_already_published(
        self, make_run: Any, stub_publisher: Any, capsys: Any
    ) -> None:
        run = make_run([make_row(1), make_row(2)], published={"TC-AB12-001": "PROJ-900"})
        approve(run, stub_publisher(), dry_run=True, json_output=True)
        payload = json.loads(capsys.readouterr().out)
        assert payload["already_published"] == ["TC-AB12-001"]
        assert [c["test_id"] for c in payload["would_create"]] == ["TC-AB12-002"]

    def test_a_dry_run_on_an_invalid_draft_still_exits_10(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        """--dry-run is not a way to skip validation; it is a way to skip writing."""
        from jira_testgen.errors import DraftValidationFailure

        run = make_run([make_row(1, kind="smoke")])
        with pytest.raises(DraftValidationFailure):
            approve(run, stub_publisher(), dry_run=True)


# ---------------------------------------------------------------------------
# T046 -- identifiers written back
# ---------------------------------------------------------------------------


class TestIdentifierWriteBack:
    def test_a_user_added_row_gets_an_identifier_in_the_file(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        run = make_run([make_row(1), make_row(2, test_id="")])
        approve(run, stub_publisher())
        ids = [row["test_id"] for row in read_draft_rows(run.draft_path)]
        assert ids == ["TC-AB12-001", "TC-AB12-002"]

    def test_re_approving_does_not_publish_the_added_row_twice(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        """The whole reason the write-back exists. Without it the second run mints a
        different identifier, the publication record no longer matches, and the case is
        created again."""
        run = make_run([make_row(1), make_row(2, test_id="")])

        first = stub_publisher()
        approve(run, first)
        assert len(first.published) == 2

        # A second approve of the same file must find both cases already recorded.
        state = RunStateStore(run.directory).load()
        ids_after = [row["test_id"] for row in read_draft_rows(run.draft_path)]
        assert set(ids_after) <= state.published_ids()

    def test_the_preamble_survives_the_write_back(self, make_run: Any, stub_publisher: Any) -> None:
        """It records the run the user reviewed. Regenerating it would rewrite history in
        the one file whose job is to be trustworthy."""
        from jira_testgen.draft.csv_io import read_preamble

        run = make_run([make_row(1), make_row(2, test_id="")])
        before = read_preamble(run.draft_path)
        approve(run, stub_publisher())
        assert read_preamble(run.draft_path) == before

    def test_no_other_cell_is_changed(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run(
            [
                make_row(1, notes="a note, with a comma"),
                make_row(2, test_id="", summary='A "quoted" summary'),
            ]
        )
        approve(run, stub_publisher())
        rows = read_draft_rows(run.draft_path)
        assert rows[0]["notes"] == "a note, with a comma"
        assert rows[1]["summary"] == 'A "quoted" summary'

    def test_no_identifier_is_rewritten_when_none_was_assigned(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        """Only ``jira_key`` may change after a publish (T065). Every other cell,
        identifiers above all, must come back exactly as the reviewer left it."""
        run = make_run([make_row(1), make_row(2)])
        before = read_draft_rows(run.draft_path)
        approve(run, stub_publisher())
        after = read_draft_rows(run.draft_path)

        for old, new in zip(before, after, strict=True):
            for column, value in old.items():
                if column != "jira_key":
                    assert new[column] == value

    def test_a_dry_run_leaves_the_file_byte_identical_apart_from_identifiers(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        """Nothing was published, so there is no jira_key to write either."""
        run = make_run([make_row(1), make_row(2)])
        before = run.draft_path.read_bytes()
        approve(run, stub_publisher(), dry_run=True)
        assert run.draft_path.read_bytes() == before

    def test_a_dry_run_also_persists_the_identifier(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        """A reviewer may dry-run first and approve later. If the two runs disagree about
        the identifier, the dry run's report described a different case than the one that
        gets published."""
        run = make_run([make_row(1), make_row(2, test_id="")])
        approve(run, stub_publisher(), dry_run=True)
        first = [row["test_id"] for row in read_draft_rows(run.draft_path)]

        approve(run, stub_publisher())
        assert [row["test_id"] for row in read_draft_rows(run.draft_path)] == first
