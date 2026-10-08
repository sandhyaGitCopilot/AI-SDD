"""T042: resuming a review in a fresh invocation (FR-017, SC-010).

The promise is specific: a reviewer who walks away from the prompt can come back later and
approve without the work being redone. So the assertions are about what *does not* happen —
no Jira read, no generation call — not merely that the second invocation succeeds. A resume
that quietly re-reads the issue and re-generates would pass a naive test while costing the
user a model call and, worse, silently swapping the content they reviewed for new content.
"""

from __future__ import annotations

from typing import Any

import pytest
import typer

from jira_testgen.commands.approve import run_approve
from jira_testgen.draft.state import RunStateStore, resolve_run
from jira_testgen.models import RunPhase

from .conftest import make_row

pytestmark = pytest.mark.integration


def approve(run: Any, publisher: Any, **kwargs: Any) -> Any:
    options: dict[str, Any] = {
        "run_id": None,  # the bare form: resolve the pending run
        "reject": False,
        "only": None,
        "dry_run": False,
        "workspace": run.workspace,
        "json_output": False,
        "publisher": publisher,
    }
    options.update(kwargs)
    return run_approve(**options)


class TestResume:
    def test_an_abandoned_draft_is_still_pending(self, make_run: Any) -> None:
        run = make_run([make_row(i) for i in range(1, 4)], phase=RunPhase.DRAFTED)
        assert resolve_run(run.workspace, None).run_id == run.run_id

    def test_a_fresh_invocation_publishes_the_abandoned_draft(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        run = make_run([make_row(i) for i in range(1, 4)], phase=RunPhase.DRAFTED)
        publisher = stub_publisher()
        with pytest.raises(typer.Exit) as exc:
            approve(run, publisher)
        assert exc.value.exit_code == 0
        assert len(publisher.published) == 3

    def test_resume_makes_no_jira_call_at_all(
        self, make_run: Any, stub_publisher: Any, jira_mock: Any
    ) -> None:
        """Not 'no write' -- no call. The snapshot in state.json is the input (FR-017)."""
        run = make_run([make_row(i) for i in range(1, 4)], phase=RunPhase.DRAFTED)
        with pytest.raises(typer.Exit):
            approve(run, stub_publisher())
        assert list(jira_mock.calls) == []

    def test_resume_makes_no_generation_call(
        self, make_run: Any, stub_publisher: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import jira_testgen.generation.engine as engine

        def explode(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("resume must never re-generate")

        monkeypatch.setattr(engine, "generate_test_cases", explode)
        run = make_run([make_row(i) for i in range(1, 4)], phase=RunPhase.DRAFTED)
        with pytest.raises(typer.Exit):
            approve(run, stub_publisher())

    def test_resume_needs_no_generation_credentials(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        """The autouse fixture has already stripped ANTHROPIC_API_KEY. Resuming must work
        anyway -- demanding a generation key to publish an existing draft would be absurd."""
        run = make_run([make_row(1)], phase=RunPhase.DRAFTED)
        with pytest.raises(typer.Exit) as exc:
            approve(run, stub_publisher())
        assert exc.value.exit_code == 0

    def test_the_reviewed_content_is_what_gets_published(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        """The CSV on disk is the input, so an edit made during the walk-away is honoured
        and nothing is silently regenerated (FR-014)."""
        run = make_run([make_row(1, summary="The summary the reviewer left behind")])
        publisher = stub_publisher()
        with pytest.raises(typer.Exit):
            approve(run, publisher)
        assert publisher.seen_summaries == ["The summary the reviewer left behind"]

    def test_the_phase_advances_to_published(self, make_run: Any, stub_publisher: Any) -> None:
        run = make_run([make_row(1)], phase=RunPhase.DRAFTED)
        with pytest.raises(typer.Exit):
            approve(run, stub_publisher())
        state = RunStateStore(run.directory).load()
        assert state.phase is RunPhase.PUBLISHED


class TestResumeIsAmbiguityAware:
    def test_two_pending_runs_refuse_to_guess(self, make_run: Any, stub_publisher: Any) -> None:
        """FR-017a: picking one silently risks publishing the wrong draft."""
        from jira_testgen.errors import NoPendingDraft

        first = make_run([make_row(1)], run_id="PROJ-123-20261006-142233")
        make_run([make_row(1)], run_id="PROJ-456-20261006-150000")
        publisher = stub_publisher()
        with pytest.raises(NoPendingDraft) as exc:
            approve(first, publisher)
        assert exc.value.exit_code == 9
        assert publisher.calls == 0
        assert "PROJ-123-20261006-142233" in exc.value.render()
        assert "PROJ-456-20261006-150000" in exc.value.render()

    def test_naming_the_run_resolves_the_ambiguity(
        self, make_run: Any, stub_publisher: Any
    ) -> None:
        first = make_run([make_row(1)], run_id="PROJ-123-20261006-142233")
        make_run([make_row(1)], run_id="PROJ-456-20261006-150000")
        publisher = stub_publisher()
        with pytest.raises(typer.Exit) as exc:
            approve(first, publisher, run_id=first.run_id)
        assert exc.value.exit_code == 0
        assert publisher.calls == 1
