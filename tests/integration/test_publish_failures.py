"""T057: every way publishing can fail (contracts/jira-api.md, FR-024, FR-025).

Each failure gets a distinct exit code so a script can branch on it (FR-028), and each
leaves the run re-runnable, because a failure that loses the record of what was already
created is how duplicates happen.

The last class is the one that protects SC-007: a write whose outcome is unknown is never
blindly retried. Retrying a `POST /issue` that may already have succeeded is the single
easiest way to file the same test case twice.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
import pytest
import typer

from jira_testgen.commands.approve import run_approve
from jira_testgen.draft.state import RunStateStore, resolve_run
from jira_testgen.errors import (
    MissingProjectPermission,
    PublishFailure,
    RetriesExhausted,
)
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


def respond(status: int, **kwargs: Any) -> Any:
    def hook(body: dict[str, Any], recorder: Any) -> httpx.Response:
        return httpx.Response(status, **kwargs)

    return hook


# ---------------------------------------------------------------------------
# 400 -- a field the project rejects
# ---------------------------------------------------------------------------


class TestFieldRejected:
    BODY: ClassVar[dict[str, Any]] = {
        "errorMessages": [],
        "errors": {"customfield_10010": "Field 'customfield_10010' is required."},
    }

    def test_it_exits_11(self, make_run: Any, jira_writes: Any) -> None:
        run = make_run([make_row(1)])
        jira_writes.on_create = respond(400, json=self.BODY)
        with pytest.raises(PublishFailure) as exc:
            approve(run)
        assert exc.value.exit_code == 11

    def test_it_names_the_rejected_field(self, make_run: Any, jira_writes: Any) -> None:
        """SC-008. 'Jira rejected the issue' sends the user to the Jira admin screens with
        nothing to look for."""
        run = make_run([make_row(1)])
        jira_writes.on_create = respond(400, json=self.BODY)
        with pytest.raises(PublishFailure) as exc:
            approve(run)
        assert "customfield_10010" in exc.value.render()

    def test_it_is_not_retried(self, make_run: Any, jira_writes: Any) -> None:
        """A 400 is deterministic. Retrying it four times wastes the user's time and
        makes the log harder to read."""
        run = make_run([make_row(1)])
        jira_writes.on_create = respond(400, json=self.BODY)
        with pytest.raises(PublishFailure):
            approve(run)
        assert jira_writes.create_attempts == 1

    def test_it_stops_rather_than_trying_every_remaining_case(
        self, make_run: Any, jira_writes: Any
    ) -> None:
        """Every case has the same shape, so they would all fail the same way. Twenty-five
        identical errors is noise, not information."""
        run = make_run([make_row(i) for i in range(1, 6)])
        jira_writes.on_create = respond(400, json=self.BODY)
        with pytest.raises(PublishFailure):
            approve(run)
        assert jira_writes.create_attempts == 1

    def test_the_run_is_left_failed_and_resumable(self, make_run: Any, jira_writes: Any) -> None:
        run = make_run([make_row(1)])
        jira_writes.on_create = respond(400, json=self.BODY)
        with pytest.raises(PublishFailure):
            approve(run)
        assert RunStateStore(run.directory).load().phase is RunPhase.FAILED
        assert resolve_run(run.workspace, None).run_id == run.run_id


# ---------------------------------------------------------------------------
# 403 -- permission lost mid-run
# ---------------------------------------------------------------------------


class TestPermissionLostMidRun:
    def test_it_exits_7_not_4(self, make_run: Any, jira_writes: Any) -> None:
        """Exit 4 means 'cannot read the source issue'. A 403 while publishing is a
        missing project permission, and the codes must not be conflated (SC-008)."""
        run = make_run([make_row(i) for i in range(1, 4)])
        jira_writes.on_create = respond(403, json={"errorMessages": ["Forbidden"]})
        with pytest.raises(MissingProjectPermission) as exc:
            approve(run)
        assert exc.value.exit_code == 7

    def test_it_names_the_target_project(self, make_run: Any, jira_writes: Any) -> None:
        run = make_run([make_row(1)])
        jira_writes.on_create = respond(403, json={"errorMessages": ["Forbidden"]})
        with pytest.raises(MissingProjectPermission) as exc:
            approve(run)
        assert "PROJ" in exc.value.render()

    def test_issues_created_before_the_403_are_kept(self, make_run: Any, jira_writes: Any) -> None:
        run = make_run([make_row(i) for i in range(1, 4)])

        def forbid_after_one(body: dict[str, Any], recorder: Any) -> Any:
            if recorder.created:
                return httpx.Response(403, json={"errorMessages": ["Forbidden"]})
            return None

        jira_writes.on_create = forbid_after_one
        with pytest.raises(MissingProjectPermission):
            approve(run)
        assert len(RunStateStore(run.directory).load().published_ids()) == 1


# ---------------------------------------------------------------------------
# Retries exhausted
# ---------------------------------------------------------------------------


class TestRetriesExhausted:
    def test_it_exits_12(self, make_run: Any, jira_writes: Any, no_backoff: list[float]) -> None:
        run = make_run([make_row(1)])
        jira_writes.on_create = respond(429, headers={"Retry-After": "1"})
        with pytest.raises(RetriesExhausted) as exc:
            approve(run)
        assert exc.value.exit_code == 12

    def test_throttling_is_retried_the_contracted_number_of_times(
        self, make_run: Any, jira_writes: Any, no_backoff: list[float]
    ) -> None:
        """429 is unambiguous -- Jira is telling us it did not process the request -- so
        unlike a bare 5xx it is safe to retry even on a write."""
        run = make_run([make_row(1)])
        jira_writes.on_create = respond(429, headers={"Retry-After": "1"})
        with pytest.raises(RetriesExhausted):
            approve(run)
        assert jira_writes.create_attempts == 5

    def test_the_run_is_left_resumable(
        self, make_run: Any, jira_writes: Any, no_backoff: list[float]
    ) -> None:
        run = make_run([make_row(1)])
        jira_writes.on_create = respond(429, headers={"Retry-After": "1"})
        with pytest.raises(RetriesExhausted):
            approve(run)
        assert RunStateStore(run.directory).load().phase is RunPhase.FAILED
        assert resolve_run(run.workspace, None).run_id == run.run_id

    def test_resuming_afterwards_publishes_normally(
        self, make_run: Any, jira_writes: Any, no_backoff: list[float]
    ) -> None:
        run = make_run([make_row(1)])
        jira_writes.on_create = respond(429, headers={"Retry-After": "1"})
        with pytest.raises(RetriesExhausted):
            approve(run)
        jira_writes.on_create = None
        assert approve(run) == 0
        assert len(jira_writes.created) == 1


# ---------------------------------------------------------------------------
# The ambiguous write -- the SC-007 guard
# ---------------------------------------------------------------------------


class TestDroppedConnection:
    def test_a_dropped_create_is_not_blindly_retried(
        self, make_run: Any, jira_writes: Any, no_backoff: list[float]
    ) -> None:
        """The write may have been applied. Retrying it is how a duplicate is filed."""
        run = make_run([make_row(1)])

        def drop(body: dict[str, Any], recorder: Any) -> Any:
            raise httpx.ConnectError("connection reset after send")

        jira_writes.on_create = drop
        with pytest.raises(PublishFailure):
            approve(run)
        assert jira_writes.create_attempts == 1

    def test_a_bare_5xx_on_a_write_is_not_retried_either(
        self, make_run: Any, jira_writes: Any, no_backoff: list[float]
    ) -> None:
        """No Retry-After means Jira is not telling us it declined to process the
        request -- so the outcome is unknown, exactly like a dropped connection."""
        run = make_run([make_row(1)])
        jira_writes.on_create = respond(500, json={"errorMessages": ["boom"]})
        with pytest.raises(PublishFailure):
            approve(run)
        assert jira_writes.create_attempts == 1

    def test_the_ambiguity_is_reported_to_the_user(
        self, make_run: Any, jira_writes: Any, no_backoff: list[float], capsys: Any
    ) -> None:
        """research R4: a human has to be told, because the tool genuinely does not know."""
        run = make_run([make_row(1)])

        def drop(body: dict[str, Any], recorder: Any) -> Any:
            raise httpx.ConnectError("connection reset after send")

        jira_writes.on_create = drop
        with pytest.raises(PublishFailure):
            approve(run)
        combined = capsys.readouterr()
        assert "may have" in (combined.out + combined.err).lower()

    def test_a_dropped_link_is_reconciled_rather_than_duplicated(
        self, make_run: Any, jira_writes: Any, no_backoff: list[float]
    ) -> None:
        """The issue was definitely created -- only the link is in doubt. The reconcile
        search finds it already linked, so nothing is created a second time."""
        run = make_run([make_row(1)])

        dropped: list[int] = []

        def drop_once(body: dict[str, Any], recorder: Any) -> Any:
            if not dropped:
                dropped.append(1)
                raise httpx.ConnectError("connection reset after send")
            return None

        jira_writes.on_link = drop_once
        with pytest.raises(PublishFailure):
            approve(run)

        assert len(jira_writes.created) == 1
        entry = RunStateStore(run.directory).load().publication_record["TC-AB12-001"]
        assert entry.issue_key is not None

        jira_writes.on_link = None
        assert approve(run) == 0
        assert len(jira_writes.created) == 1

    def test_a_recovered_create_is_adopted_not_recreated(
        self, make_run: Any, jira_writes: Any, no_backoff: list[float]
    ) -> None:
        """The reconcile search finds an issue whose summary matches, so the tool adopts
        it instead of filing a second one."""
        run = make_run([make_row(1, summary="A case that really was created")])

        def create_then_drop(body: dict[str, Any], recorder: Any) -> Any:
            # Record it as created -- the server did the work -- then lose the response.
            recorder.created.append(
                {"key": "QA-777", "summary": body["fields"]["summary"], "body": body}
            )
            raise httpx.ConnectError("response lost after the issue was created")

        jira_writes.on_create = create_then_drop
        approve(run)

        state = RunStateStore(run.directory).load()
        assert state.publication_record["TC-AB12-001"].issue_key == "QA-777"
        assert len(jira_writes.created) == 1


# ---------------------------------------------------------------------------
# Per-case failure capture
# ---------------------------------------------------------------------------


class TestPerCaseFailureCapture:
    def test_a_failure_is_recorded_against_the_case(
        self, make_run: Any, jira_writes: Any, no_backoff: list[float]
    ) -> None:
        """T063. Without this, a failed run tells the user that something went wrong but
        not which case to look at."""
        run = make_run([make_row(1)])
        jira_writes.on_create = respond(500, json={"errorMessages": ["boom"]})
        with pytest.raises(PublishFailure):
            approve(run)
        entry = RunStateStore(run.directory).load().publication_record["TC-AB12-001"]
        assert entry.last_error

    def test_the_recorded_error_contains_no_credential(
        self, make_run: Any, jira_writes: Any, no_backoff: list[float], jira_env: Any
    ) -> None:
        """FR-027. state.json is written to disk and may be shared when reporting a bug."""
        run = make_run([make_row(1)])
        jira_writes.on_create = respond(500, json={"errorMessages": ["boom"]})
        with pytest.raises(PublishFailure):
            approve(run)
        raw = (run.directory / "state.json").read_text(encoding="utf-8")
        assert jira_env["JIRA_API_TOKEN"] not in raw
