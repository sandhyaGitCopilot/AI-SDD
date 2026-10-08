"""T055: the SC-007 test -- an interrupted publish resumes without duplicating.

This is the single most important test in the feature. Jira's create-issue endpoint takes
no idempotency key (research R4), so nothing on the server prevents a second run from
filing the same test case twice. The only defence is the local publication record, written
after *each* create rather than batched at the end.

The test interrupts a run partway through, re-runs it, and checks the thing a user would
actually notice: the total count, and that no summary appears twice in Jira.
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


class ProcessDied(Exception):
    """Stands in for the process going away mid-run: a kill, a laptop lid, a power cut."""


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


def die_after(n: int) -> Any:
    """A create hook that lets ``n`` creates through and then kills the process."""

    def hook(body: dict[str, Any], recorder: Any) -> None:
        if len(recorder.created) >= n:
            raise ProcessDied("the process went away after the create succeeded")
        return None

    return hook


@pytest.fixture
def five_cases(make_run: Any) -> Any:
    return make_run([make_row(i) for i in range(1, 6)])


class TestInterruptedPublish:
    def test_the_interruption_is_not_swallowed(self, five_cases: Any, jira_writes: Any) -> None:
        jira_writes.on_create = die_after(2)
        with pytest.raises(ProcessDied):
            approve(five_cases)

    def test_issues_created_before_the_interruption_are_recorded(
        self, five_cases: Any, jira_writes: Any
    ) -> None:
        """Recorded *during* the run, not at the end. Batching the write would lose all
        five records here instead of none."""
        jira_writes.on_create = die_after(2)
        with pytest.raises(ProcessDied):
            approve(five_cases)
        state = RunStateStore(five_cases.directory).load()
        assert len(state.published_ids()) == 2

    def test_the_run_is_left_resumable(self, five_cases: Any, jira_writes: Any) -> None:
        from jira_testgen.draft.state import resolve_run

        jira_writes.on_create = die_after(2)
        with pytest.raises(ProcessDied):
            approve(five_cases)
        assert RunStateStore(five_cases.directory).load().phase is RunPhase.FAILED
        assert resolve_run(five_cases.workspace, None).run_id == five_cases.run_id


class TestResumeCreatesOnlyTheOutstanding:
    def test_only_the_remaining_cases_are_created(self, five_cases: Any, jira_writes: Any) -> None:
        jira_writes.on_create = die_after(2)
        with pytest.raises(ProcessDied):
            approve(five_cases)

        jira_writes.on_create = None
        before = jira_writes.create_attempts
        assert approve(five_cases) == 0
        assert jira_writes.create_attempts - before == 3

    def test_the_total_equals_the_approved_count(self, five_cases: Any, jira_writes: Any) -> None:
        jira_writes.on_create = die_after(2)
        with pytest.raises(ProcessDied):
            approve(five_cases)
        jira_writes.on_create = None
        approve(five_cases)
        assert len(jira_writes.created) == 5

    def test_no_summary_is_published_twice(self, five_cases: Any, jira_writes: Any) -> None:
        """SC-007 as a user would check it: look at Jira and count."""
        jira_writes.on_create = die_after(2)
        with pytest.raises(ProcessDied):
            approve(five_cases)
        jira_writes.on_create = None
        approve(five_cases)
        summaries = jira_writes.created_summaries
        assert len(summaries) == len(set(summaries))

    def test_every_case_ends_up_linked(self, five_cases: Any, jira_writes: Any) -> None:
        jira_writes.on_create = die_after(2)
        with pytest.raises(ProcessDied):
            approve(five_cases)
        jira_writes.on_create = None
        approve(five_cases)
        state = RunStateStore(five_cases.directory).load()
        assert state.entries_needing_link() == []
        assert len(jira_writes.links) == 5

    def test_interrupting_twice_still_does_not_duplicate(
        self, five_cases: Any, jira_writes: Any
    ) -> None:
        jira_writes.on_create = die_after(1)
        with pytest.raises(ProcessDied):
            approve(five_cases)
        jira_writes.on_create = die_after(3)
        with pytest.raises(ProcessDied):
            approve(five_cases)
        jira_writes.on_create = None
        approve(five_cases)
        assert len(jira_writes.created) == 5
        assert len(set(jira_writes.created_summaries)) == 5


class TestCrashBetweenCreateAndLink:
    def test_a_created_but_unlinked_issue_is_recorded_as_such(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        """The two calls fail apart, so they are recorded apart (research R4)."""
        run = make_run([make_row(1)])

        def die_on_link(body: dict[str, Any], recorder: Any) -> None:
            raise ProcessDied("died between create and link")

        jira_writes.on_link = die_on_link
        with pytest.raises(ProcessDied):
            approve(run)

        state = RunStateStore(run.directory).load()
        entry = state.publication_record["TC-AB12-001"]
        assert entry.issue_key is not None
        assert entry.linked is False

    def test_resuming_repairs_the_link_without_creating_a_second_issue(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        """FR-023, FR-020. The repair pass is what makes this state temporary rather than
        a permanently orphaned issue."""
        run = make_run([make_row(1)])

        def die_on_link(body: dict[str, Any], recorder: Any) -> None:
            raise ProcessDied("died between create and link")

        jira_writes.on_link = die_on_link
        with pytest.raises(ProcessDied):
            approve(run)

        jira_writes.on_link = None
        assert approve(run) == 0
        assert len(jira_writes.created) == 1
        assert len(jira_writes.links) == 1
        assert RunStateStore(run.directory).load().entries_needing_link() == []

    def test_repair_happens_before_new_cases_are_created(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        """T061. An orphan issue is the more urgent problem: leaving it until after five
        new creates widens the window in which a second interruption strands it again."""
        run = make_run([make_row(1), make_row(2)])

        # First run: case 1 is created, then the process dies linking it, leaving one
        # orphan and one case not started at all.
        def die_linking(body: dict[str, Any], recorder: Any) -> None:
            raise ProcessDied("died linking the first case")

        jira_writes.on_link = die_linking
        with pytest.raises(ProcessDied):
            approve(run)
        assert len(jira_writes.created) == 1

        # Second run: record the order of calls. The repair must come first.
        order: list[str] = []

        def note_create(body: dict[str, Any], recorder: Any) -> None:
            order.append("create")
            return None

        def note_link(body: dict[str, Any], recorder: Any) -> None:
            order.append("link")
            return None

        jira_writes.on_create = note_create
        jira_writes.on_link = note_link
        approve(run)

        assert order[0] == "link", order
        assert order == ["link", "create", "link"], order
