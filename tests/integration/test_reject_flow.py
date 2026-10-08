"""T044: rejecting a draft (FR-015, SC-006).

Rejection is a success, not an error. The user looked at the draft and decided against it,
which is the review gate working exactly as designed -- so it exits `0`. The three things
that must hold afterwards: nothing in Jira, the draft still on disk, and the run marked
rejected so it stops appearing as outstanding work.
"""

from __future__ import annotations

from typing import Any

import pytest
import typer

from jira_testgen.commands.approve import run_approve
from jira_testgen.draft.state import RunStateStore
from jira_testgen.models import RunPhase

from .conftest import make_row

pytestmark = pytest.mark.integration


def reject(run: Any, publisher: Any, **kwargs: Any) -> Any:
    options: dict[str, Any] = {
        "run_id": run.run_id,
        "reject": True,
        "only": None,
        "dry_run": False,
        "workspace": run.workspace,
        "json_output": False,
        "publisher": publisher,
    }
    options.update(kwargs)
    with pytest.raises(typer.Exit) as exc:
        run_approve(**options)
    return exc.value


class TestRejectWholeDraft:
    def test_it_exits_0(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run([make_row(i) for i in range(1, 4)])
        assert reject(run, stub_publisher()).exit_code == 0

    def test_it_creates_nothing(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run([make_row(i) for i in range(1, 4)])
        publisher = stub_publisher()
        reject(run, publisher)
        assert publisher.calls == 0
        assert publisher.published == []

    def test_it_makes_no_jira_call_at_all(
        self, make_run: Any, stub_publisher: Any, jira_mock: Any
    ) -> None:
        run = make_run([make_row(i) for i in range(1, 4)])
        reject(run, stub_publisher())
        assert list(jira_mock.calls) == []

    def test_the_draft_stays_on_disk_for_reuse(self, make_run: Any, stub_publisher: Any) -> None:
        """FR-015: the reviewer may want to edit and re-approve, or keep it as a record."""
        run = make_run([make_row(i) for i in range(1, 4)])
        before = run.draft_path.read_bytes()
        reject(run, stub_publisher())
        assert run.draft_path.exists()
        assert run.draft_path.read_bytes() == before

    def test_the_run_is_marked_rejected(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run([make_row(1)])
        reject(run, stub_publisher())
        assert RunStateStore(run.directory).load().phase is RunPhase.REJECTED

    def test_a_rejected_run_no_longer_counts_as_pending(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        """Otherwise a bare `approve` keeps offering a draft the user already said no to."""
        from jira_testgen.errors import NoPendingDraft

        run = make_run([make_row(1)])
        reject(run, stub_publisher())
        with pytest.raises(NoPendingDraft):
            run_approve(
                run_id=None,
                reject=False,
                only=None,
                dry_run=False,
                workspace=run.workspace,
                json_output=False,
                publisher=stub_publisher(),
            )

    def test_it_still_appears_in_the_drafts_listing(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        """Rejected is a recorded outcome, not a deletion."""
        from jira_testgen.draft.state import list_runs

        run = make_run([make_row(1)])
        reject(run, stub_publisher())
        listing = list_runs(run.workspace)
        assert [r.phase for r in listing] == [RunPhase.REJECTED]

    def test_rejection_does_not_require_a_valid_draft(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        """Refusing to let a user reject a malformed draft would trap them: the only way
        out would be to fix a file they have already decided to throw away."""
        run = make_run([make_row(1, kind="smoke")])
        assert reject(run, stub_publisher()).exit_code == 0
        assert RunStateStore(run.directory).load().phase is RunPhase.REJECTED

    def test_json_output_reports_the_rejection(
        self, make_run: Any, stub_publisher: Any, capsys: Any
    ) -> None:
        import json

        run = make_run([make_row(1)])
        reject(run, stub_publisher(), json_output=True)
        payload = json.loads(capsys.readouterr().out)
        assert payload["phase"] == "rejected"
        assert payload["created"] == []


class TestPerRowRejection:
    def test_rows_marked_rejected_are_not_published(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        run = make_run(
            [
                make_row(1, approval="approved"),
                make_row(2, approval="rejected"),
                make_row(3, approval="approved"),
            ]
        )
        publisher = stub_publisher()
        with pytest.raises(typer.Exit) as exc:
            run_approve(
                run_id=run.run_id,
                reject=False,
                only=None,
                dry_run=False,
                workspace=run.workspace,
                json_output=False,
                publisher=publisher,
            )
        assert exc.value.exit_code == 0
        assert publisher.published == ["TC-AB12-001", "TC-AB12-003"]

    def test_every_row_rejected_exits_0_with_nothing_to_publish(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        """FR-018: nothing to publish is a success. Exiting non-zero here would make a
        scripted caller treat a deliberate decision as a failure."""
        run = make_run([make_row(i, approval="rejected") for i in range(1, 4)])
        publisher = stub_publisher()
        with pytest.raises(typer.Exit) as exc:
            run_approve(
                run_id=run.run_id,
                reject=False,
                only=None,
                dry_run=False,
                workspace=run.workspace,
                json_output=False,
                publisher=publisher,
            )
        assert exc.value.exit_code == 0
        assert publisher.calls == 0

    def test_all_pending_also_exits_0_with_nothing_to_publish(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        """A reviewer who ran `approve` without editing the column has approved nothing.
        Publishing all of it would be precisely the FR-013 violation this tool exists to
        prevent."""
        run = make_run([make_row(i, approval="pending") for i in range(1, 4)])
        publisher = stub_publisher()
        with pytest.raises(typer.Exit) as exc:
            run_approve(
                run_id=run.run_id,
                reject=False,
                only=None,
                dry_run=False,
                workspace=run.workspace,
                json_output=False,
                publisher=publisher,
            )
        assert exc.value.exit_code == 0
        assert publisher.calls == 0
