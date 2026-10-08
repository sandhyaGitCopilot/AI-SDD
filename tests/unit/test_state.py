"""T018: run state persistence (research R4).

These tests exist because the no-duplicates guarantee in SC-007 is a property of this file.
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

import pytest

from jira_testgen.draft.state import (
    STATE_FILENAME,
    RunStateStore,
    build_run_id,
    list_runs,
    resolve_run,
)
from jira_testgen.errors import NoPendingDraft
from jira_testgen.models import RunPhase, RunState, SourceRequirement

pytestmark = pytest.mark.unit


def make_state(run_id: str = "PROJ-123-20261006-120000") -> RunState:
    return RunState(
        run_id=run_id,
        source_snapshot=SourceRequirement(
            issue_key="PROJ-123",
            summary="Password reset",
            description_text="Users can reset their password.",
            project_key="PROJ",
        ),
        draft_path="testcases.csv",
        target_project_key="QA",
    )


class TestRoundTrip:
    def test_save_then_load(self, tmp_path: Path) -> None:
        store = RunStateStore(tmp_path)
        store.save(make_state())
        loaded = store.load()
        assert loaded.run_id == "PROJ-123-20261006-120000"
        assert loaded.source_snapshot.issue_key == "PROJ-123"
        assert loaded.schema_version == 1

    def test_load_without_state_raises_actionable_error(self, tmp_path: Path) -> None:
        with pytest.raises(NoPendingDraft) as exc:
            RunStateStore(tmp_path).load()
        assert exc.value.exit_code == 9
        assert "drafts" in exc.value.remediation

    def test_source_snapshot_survives_so_resume_needs_no_jira_read(self, tmp_path: Path) -> None:
        """FR-017: resume must not re-read Jira."""
        store = RunStateStore(tmp_path)
        state = make_state()
        state.source_snapshot.description_text = "Original text at generation time."
        store.save(state)
        assert store.load().source_snapshot.description_text == "Original text at generation time."


class TestAtomicWrites:
    def test_no_temp_files_left_behind(self, tmp_path: Path) -> None:
        store = RunStateStore(tmp_path)
        for _ in range(5):
            store.save(make_state())
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".state-")]
        assert leftovers == []

    def test_existing_state_survives_a_failed_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A crash mid-write must not truncate the record that protects against duplicates."""
        store = RunStateStore(tmp_path)
        state = make_state()
        store.record_issue_created(state, "TC-7F3A-001", "QA-1", "https://x/QA-1")
        good = store.path.read_text(encoding="utf-8")

        def explode(*_: object, **__: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("jira_testgen.draft.state.os.replace", explode)
        with pytest.raises(OSError, match="disk full"):
            store.save(state)

        # The previous record is intact and still parseable.
        assert store.path.read_text(encoding="utf-8") == good
        assert json.loads(good)["publication_record"]["TC-7F3A-001"]["issue_key"] == "QA-1"
        assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".state-")] == []


class TestPublicationRecord:
    def test_issue_key_is_persisted_immediately(self, tmp_path: Path) -> None:
        """Written on create, before linking -- bounds crash damage to one case."""
        store = RunStateStore(tmp_path)
        state = make_state()
        store.record_issue_created(state, "TC-7F3A-001", "QA-1", "https://x/QA-1")

        reloaded = RunStateStore(tmp_path).load()
        entry = reloaded.publication_record["TC-7F3A-001"]
        assert entry.issue_key == "QA-1"
        assert entry.linked is False, "linking is a separate call and must not be assumed"

    def test_link_recorded_separately(self, tmp_path: Path) -> None:
        store = RunStateStore(tmp_path)
        state = make_state()
        store.record_issue_created(state, "TC-7F3A-001", "QA-1", "https://x/QA-1")
        store.record_linked(state, "TC-7F3A-001")
        assert RunStateStore(tmp_path).load().publication_record["TC-7F3A-001"].linked is True

    def test_crash_between_create_and_link_is_recoverable(self, tmp_path: Path) -> None:
        store = RunStateStore(tmp_path)
        state = make_state()
        store.record_issue_created(state, "TC-7F3A-001", "QA-1", "https://x/QA-1")

        recovered = RunStateStore(tmp_path).load()
        # Not re-created...
        assert recovered.outstanding(["TC-7F3A-001"]) == []
        # ...but the missing link is known and repairable.
        assert [e.test_id for e in recovered.entries_needing_link()] == ["TC-7F3A-001"]

    def test_partial_publish_leaves_only_outstanding_cases(self, tmp_path: Path) -> None:
        """The SC-007 skip logic, at the storage layer."""
        store = RunStateStore(tmp_path)
        state = make_state()
        store.record_issue_created(state, "TC-7F3A-001", "QA-1", "https://x/QA-1")
        store.record_issue_created(state, "TC-7F3A-002", "QA-2", "https://x/QA-2")

        reloaded = RunStateStore(tmp_path).load()
        approved = ["TC-7F3A-001", "TC-7F3A-002", "TC-7F3A-003"]
        assert reloaded.outstanding(approved) == ["TC-7F3A-003"]

    def test_record_failure_keeps_case_outstanding(self, tmp_path: Path) -> None:
        store = RunStateStore(tmp_path)
        state = make_state()
        store.record_failure(state, "TC-7F3A-001", "HTTP 400: field required")
        reloaded = RunStateStore(tmp_path).load()
        assert reloaded.publication_record["TC-7F3A-001"].issue_key is None
        assert reloaded.outstanding(["TC-7F3A-001"]) == ["TC-7F3A-001"]

    def test_linking_unknown_test_id_is_a_programming_error(self, tmp_path: Path) -> None:
        store = RunStateStore(tmp_path)
        with pytest.raises(ValueError, match="unknown test id"):
            store.record_linked(make_state(), "TC-7F3A-999")


class TestRunDiscovery:
    def _make_run(self, workspace: Path, run_id: str, phase: RunPhase) -> None:
        directory = workspace / "runs" / run_id
        directory.mkdir(parents=True)
        state = make_state(run_id)
        state.phase = phase
        RunStateStore(directory).save(state)
        (directory / "testcases.csv").write_text(
            "# preamble\ntest_id,summary\nTC-7F3A-001,x\nTC-7F3A-002,y\n", encoding="utf-8-sig"
        )

    def test_empty_workspace_lists_nothing(self, tmp_path: Path) -> None:
        assert list_runs(tmp_path) == []

    def test_counts_rows_excluding_preamble_and_header(self, tmp_path: Path) -> None:
        self._make_run(tmp_path, "PROJ-1-20261006-120000", RunPhase.DRAFTED)
        assert list_runs(tmp_path)[0].case_count == 2

    def test_corrupt_run_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        self._make_run(tmp_path, "PROJ-1-20261006-120000", RunPhase.DRAFTED)
        broken = tmp_path / "runs" / "PROJ-2-20261006-130000"
        broken.mkdir(parents=True)
        (broken / STATE_FILENAME).write_text("{not json", encoding="utf-8")

        runs = list_runs(tmp_path)
        assert [r.run_id for r in runs] == ["PROJ-1-20261006-120000"]

    def test_resolves_the_only_pending_run(self, tmp_path: Path) -> None:
        self._make_run(tmp_path, "PROJ-1-20261006-120000", RunPhase.DRAFTED)
        assert resolve_run(tmp_path, None).run_id == "PROJ-1-20261006-120000"

    def test_refuses_to_guess_between_several_pending_runs(self, tmp_path: Path) -> None:
        """FR-017a: guessing risks publishing the wrong draft to a shared project."""
        self._make_run(tmp_path, "PROJ-1-20261006-120000", RunPhase.DRAFTED)
        self._make_run(tmp_path, "PROJ-2-20261006-130000", RunPhase.DRAFTED)

        with pytest.raises(NoPendingDraft) as exc:
            resolve_run(tmp_path, None)

        assert "name the one to act on" in exc.value.message
        assert "PROJ-1-20261006-120000" in exc.value.remediation
        assert "PROJ-2-20261006-130000" in exc.value.remediation

    def test_published_runs_are_not_pending(self, tmp_path: Path) -> None:
        self._make_run(tmp_path, "PROJ-1-20261006-120000", RunPhase.PUBLISHED)
        with pytest.raises(NoPendingDraft, match="No drafts are awaiting review"):
            resolve_run(tmp_path, None)

    def test_failed_run_stays_pending_so_it_can_be_resumed(self, tmp_path: Path) -> None:
        self._make_run(tmp_path, "PROJ-1-20261006-120000", RunPhase.FAILED)
        assert resolve_run(tmp_path, None).phase is RunPhase.FAILED

    def test_unknown_run_id_lists_what_exists(self, tmp_path: Path) -> None:
        self._make_run(tmp_path, "PROJ-1-20261006-120000", RunPhase.DRAFTED)
        with pytest.raises(NoPendingDraft) as exc:
            resolve_run(tmp_path, "PROJ-9-nope")
        assert "PROJ-1-20261006-120000" in exc.value.remediation


def test_run_id_shape() -> None:
    from datetime import datetime

    when = datetime(2026, 10, 6, 14, 22, 33, tzinfo=UTC)
    assert build_run_id("PROJ-123", when) == "PROJ-123-20261006-142233"


class TestRunListingCaseCount:
    """The listing's case count is the number a reviewer uses to budget their time, so
    counting physical lines instead of records (every draft has multi-line `steps`) is a
    wrong answer, not an approximation."""

    def test_multi_line_cells_do_not_inflate_the_count(self, tmp_path: Path) -> None:
        from jira_testgen.draft.state import _count_rows

        draft = tmp_path / "testcases.csv"
        draft.write_bytes(
            b"\xef\xbb\xbf# jira-testgen draft v1\r\n"
            b"test_id,approval,kind,summary,preconditions,steps,expected_results,"
            b"traces_to,jira_key,notes\r\n"
            b'TC-AB12-001,approved,positive,One,,"1. A\r\n2. B\r\n3. C","1. X",AC-1,,\r\n'
            b'TC-AB12-002,approved,negative,Two,,"1. A\r\n2. B","1. X\r\n2. Y",AC-2,,\r\n'
        )
        assert _count_rows(tmp_path) == 2

    def test_a_missing_draft_counts_zero(self, tmp_path: Path) -> None:
        from jira_testgen.draft.state import _count_rows

        assert _count_rows(tmp_path) == 0

    def test_an_unreadable_draft_does_not_break_the_listing(self, tmp_path: Path) -> None:
        from jira_testgen.draft.state import _count_rows

        (tmp_path / "testcases.csv").write_bytes(b"# preamble only\r\n")
        assert _count_rows(tmp_path) == 0
