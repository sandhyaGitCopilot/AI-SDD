"""T041: draft validation rules V1-V11 reaching the user (contracts/draft-csv.md).

``tests/unit/test_validate.py`` proves each rule fires. This file proves the consequence:
every fatal rule exits `10` and **publishes nothing**. The second half is the part worth
testing. A validator that reports a problem and then publishes anyway is worse than no
validator, because the user has been told they are protected.
"""

from __future__ import annotations

from typing import Any

import pytest
import typer

from jira_testgen.commands.approve import run_approve
from jira_testgen.errors import DraftValidationFailure

from .conftest import make_row, render_csv

pytestmark = pytest.mark.integration


def approve(run: Any, publisher: Any, **kwargs: Any) -> Any:
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
    return run_approve(**options)


def expect_validation_failure(run: Any, publisher: Any, **kwargs: Any) -> DraftValidationFailure:
    with pytest.raises(DraftValidationFailure) as exc:
        approve(run, publisher, **kwargs)
    assert exc.value.exit_code == 10
    assert publisher.calls == 0, "a failed validation must not reach the publisher"
    return exc.value


class TestV1MissingColumn:
    def test_missing_required_column_exits_10(self, make_run: Any, stub_publisher: Any) -> None:
        columns = [c for c in ["test_id", "approval", "kind", "summary", "steps"]]
        run = make_run([make_row(1)], columns=columns)
        publisher = stub_publisher()
        error = expect_validation_failure(run, publisher)
        assert "expected_results" in str(error)
        assert "traces_to" in str(error)

    def test_a_renamed_column_is_reported_by_name(self, make_run: Any, stub_publisher: Any) -> None:
        columns = [
            "test_id",
            "approval",
            "kind",
            "title",  # renamed from summary
            "preconditions",
            "steps",
            "expected_results",
            "traces_to",
            "jira_key",
            "notes",
        ]
        run = make_run([make_row(1)], columns=columns)
        error = expect_validation_failure(run, stub_publisher())
        assert "summary" in str(error)


class TestV2DuplicateIdentifier:
    def test_duplicate_exits_10(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run([make_row(1), make_row(2, test_id="TC-AB12-001")])
        error = expect_validation_failure(run, stub_publisher())
        assert any(f.split(" ")[0] == "V2" or "duplicate" in f.lower() for f in error.findings)

    def test_duplicate_names_both_rows(self, make_run: Any, stub_publisher: Any) -> None:
        """FR-016: one row number sends the user hunting for the other."""
        run = make_run([make_row(1), make_row(2, test_id="TC-AB12-001")])
        error = expect_validation_failure(run, stub_publisher())
        rendered = error.render()
        # 8 preamble lines + the header put row 1 on line 10; its multi-line cells make
        # it span three lines, so the duplicate is on line 13. Both must be named.
        assert "row 13" in rendered
        assert "row 10" in rendered


class TestV3ApprovalValue:
    def test_bad_approval_exits_10(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run([make_row(1, approval="yes please")])
        error = expect_validation_failure(run, stub_publisher())
        assert "approval" in error.render()


class TestV5Summary:
    def test_empty_summary_exits_10(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run([make_row(1, summary="")])
        error = expect_validation_failure(run, stub_publisher())
        assert "summary" in error.render()

    def test_oversize_summary_exits_10(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run([make_row(1, summary="x" * 300)])
        expect_validation_failure(run, stub_publisher())


class TestV6Steps:
    def test_zero_steps_exits_10(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run([make_row(1, steps="", expected_results="1. Something happens")])
        error = expect_validation_failure(run, stub_publisher())
        assert "steps" in error.render()


class TestV7ExpectedResults:
    def test_mismatched_count_exits_10(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run([make_row(1, steps="1. A\n2. B\n3. C", expected_results="1. X\n2. Y")])
        error = expect_validation_failure(run, stub_publisher())
        assert "expected_results" in error.render()


class TestV10RowCap:
    def test_over_25_rows_exits_10(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run([make_row(i) for i in range(1, 27)])
        error = expect_validation_failure(run, stub_publisher())
        assert "25" in error.render()


class TestV11Unreadable:
    def test_unbalanced_quote_exits_10(self, make_run: Any, stub_publisher: Any) -> None:
        body = render_csv([make_row(1)])
        broken = body.replace(b"TC-AB12-001", b'"TC-AB12-001')
        run = make_run(raw_csv=broken)
        expect_validation_failure(run, stub_publisher())

    def test_a_deleted_draft_exits_10(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run([make_row(1)])
        run.draft_path.unlink()
        expect_validation_failure(run, stub_publisher())

    def test_an_emptied_draft_exits_10(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run([make_row(1)])
        run.draft_path.write_bytes(b"")
        expect_validation_failure(run, stub_publisher())


class TestWarningsDoNotBlock:
    def test_unknown_criterion_is_a_warning_and_publishing_continues(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        """V8 is a warning on purpose -- a reviewer may trace a case they wrote to nothing
        the tool knows about, and refusing to publish it would be the tool overruling them."""
        run = make_run([make_row(1, traces_to="AC-99")])
        publisher = stub_publisher()
        with pytest.raises(typer.Exit) as exc:
            approve(run, publisher)
        assert exc.value.exit_code == 0
        assert publisher.published == ["TC-AB12-001"]

    def test_a_deleted_published_row_is_a_warning(self, make_run: Any, stub_publisher: Any) -> None:
        """V9: deleting an already-published row is reasonable. It must be visible, not
        forbidden."""
        run = make_run([make_row(1)], published={"TC-AB12-002": "PROJ-900"})
        publisher = stub_publisher()
        with pytest.raises(typer.Exit) as exc:
            approve(run, publisher)
        assert exc.value.exit_code == 0


class TestTheDraftSurvivesAFailure:
    def test_the_file_is_left_on_disk_for_the_user_to_fix(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        """FR-016: a failed validation must leave the draft intact and re-approvable."""
        run = make_run([make_row(1, kind="smoke")])
        before = run.draft_path.read_bytes()
        expect_validation_failure(run, stub_publisher())
        assert run.draft_path.read_bytes() == before

    def test_the_run_stays_resolvable_after_a_failure(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        from jira_testgen.draft.state import resolve_run

        run = make_run([make_row(1, kind="smoke")])
        expect_validation_failure(run, stub_publisher())
        assert resolve_run(run.workspace, None).run_id == run.run_id
