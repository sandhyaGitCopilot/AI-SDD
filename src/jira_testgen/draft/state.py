"""Run state persistence -- the duplicate guard (research R4).

``state.json`` is tool-owned and authoritative. It is deliberately *not* the CSV: FR-014
invites the user to edit the draft freely and a spreadsheet may rewrite it wholesale, so the
one record that must survive exactly cannot live in the file the user is told to edit.

Two properties make the SC-007 no-duplicates guarantee hold:

1. **Atomic writes.** Every mutation goes to a temp file in the same directory and is then
   ``os.replace``d in. A crash mid-write cannot leave truncated JSON and lose the record.
2. **Write-per-create.** The caller records each issue key the moment creation succeeds,
   before linking and before moving on. Batching at the end would widen crash damage from
   one ambiguous case to the whole run.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from jira_testgen.errors import NoPendingDraft
from jira_testgen.models import PublicationEntry, RunPhase, RunState, utcnow

logger = logging.getLogger("jira_testgen.draft.state")

STATE_FILENAME = "state.json"
DRAFT_FILENAME = "testcases.csv"
RUNS_DIRNAME = "runs"

#: Phases where a draft is still waiting for the user.
PENDING_PHASES = frozenset(
    {RunPhase.DRAFTED, RunPhase.APPROVED, RunPhase.PUBLISHING, RunPhase.FAILED}
)


def runs_root(workspace: Path) -> Path:
    return workspace / RUNS_DIRNAME


def run_dir(workspace: Path, run_id: str) -> Path:
    return runs_root(workspace) / run_id


def build_run_id(issue_key: str, when: datetime | None = None) -> str:
    stamp = (when or utcnow()).strftime("%Y%m%d-%H%M%S")
    return f"{issue_key}-{stamp}"


class RunStateStore:
    """Load and persist one run's ``state.json``."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / STATE_FILENAME

    # -- persistence ----------------------------------------------------

    def save(self, state: RunState) -> None:
        """Write atomically: temp file in the same directory, then replace.

        Same directory matters -- ``os.replace`` is only atomic within a filesystem.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        state.updated_at = utcnow()
        payload = state.model_dump_json(indent=2)

        # delete=False is required: the file must outlive the handle so os.replace can move
        # it into place. The `with handle` below closes it, and the except arm unlinks it on
        # any failure, so nothing is leaked. SIM115 cannot see that shape.
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
            mode="w",
            encoding="utf-8",
            dir=self.directory,
            prefix=".state-",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = Path(handle.name)
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def load(self) -> RunState:
        if not self.path.exists():
            raise NoPendingDraft(
                f"No run state found at {self.path}.",
                "Run `jira-testgen drafts` to see which runs exist, or `jira-testgen generate "
                "<ISSUE-KEY>` to start a new one.",
            )
        raw = self.path.read_text(encoding="utf-8")
        return RunState.model_validate_json(raw)

    def exists(self) -> bool:
        return self.path.exists()

    # -- mutations ------------------------------------------------------

    def set_phase(self, state: RunState, phase: RunPhase) -> RunState:
        state.phase = phase
        self.save(state)
        return state

    def record_issue_created(
        self, state: RunState, test_id: str, issue_key: str, issue_url: str
    ) -> RunState:
        """Record a created issue immediately, before the link is attempted (research R4).

        Persisted synchronously on purpose. An in-memory update flushed later is exactly the
        gap through which duplicates appear.
        """
        entry = state.publication_record.get(test_id) or PublicationEntry(test_id=test_id)
        entry.issue_key = issue_key
        entry.issue_url = issue_url
        entry.last_error = None
        state.publication_record[test_id] = entry
        self.save(state)
        logger.info("Recorded %s -> %s", test_id, issue_key)
        return state

    def record_linked(self, state: RunState, test_id: str) -> RunState:
        """Record the link separately -- create and link are two calls that can fail apart."""
        entry = state.publication_record.get(test_id)
        if entry is None:
            raise ValueError(f"cannot record a link for unknown test id {test_id!r}")
        entry.linked = True
        entry.last_error = None
        state.publication_record[test_id] = entry
        self.save(state)
        return state

    # -- run phase transitions (T066) -----------------------------------

    def begin_publishing(self, state: RunState) -> RunState:
        """Mark the run as in flight before the first write.

        Persisted first so that a process killed mid-publish leaves `publishing` on disk
        rather than `drafted`. The difference matters to the user: `drafted` claims
        nothing was attempted, and here something was.
        """
        return self.set_phase(state, RunPhase.PUBLISHING)

    def finish_publishing(self, state: RunState, *, failed: bool) -> RunState:
        """Settle the run: `published` when everything landed, `failed` otherwise.

        `failed` is not terminal in the sense of being final -- it stays in
        ``PENDING_PHASES`` so `drafts` keeps showing it and a bare `approve` can resume
        it. A partial publish must be re-runnable, and the publication record makes the
        re-run skip whatever already succeeded.
        """
        return self.set_phase(state, RunPhase.FAILED if failed else RunPhase.PUBLISHED)

    def record_failure(self, state: RunState, test_id: str, error: str) -> RunState:
        entry = state.publication_record.get(test_id) or PublicationEntry(test_id=test_id)
        entry.last_error = error
        state.publication_record[test_id] = entry
        self.save(state)
        return state


class RunSummary:
    """One row of the `drafts` listing (FR-017a)."""

    def __init__(self, state: RunState, directory: Path, case_count: int) -> None:
        self.state = state
        self.directory = directory
        self.case_count = case_count

    @property
    def run_id(self) -> str:
        return self.state.run_id

    @property
    def issue_key(self) -> str:
        return self.state.source_snapshot.issue_key

    @property
    def phase(self) -> RunPhase:
        return self.state.phase

    @property
    def published_count(self) -> int:
        return len(self.state.published_ids())

    @property
    def is_pending(self) -> bool:
        return self.state.phase in PENDING_PHASES

    @property
    def age(self) -> timedelta:
        return utcnow() - self.state.created_at

    @property
    def age_label(self) -> str:
        """Age as a reviewer reads it. Staleness is the judgement the listing supports:
        a draft from three weeks ago may describe a requirement that has since changed."""
        seconds = int(self.age.total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"


def list_runs(workspace: Path) -> list[RunSummary]:
    """Every run in the workspace, newest first. Unreadable runs are skipped, not fatal."""
    root = runs_root(workspace)
    if not root.exists():
        return []

    summaries: list[RunSummary] = []
    for directory in sorted(root.iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        store = RunStateStore(directory)
        if not store.exists():
            continue
        try:
            state = store.load()
        except Exception:
            logger.warning("Skipping unreadable run state in %s", directory)
            continue
        summaries.append(RunSummary(state, directory, _count_rows(directory)))

    summaries.sort(key=lambda s: s.state.created_at, reverse=True)
    return summaries


def _count_rows(directory: Path) -> int:
    """Count the test cases in a draft, for the listing.

    This parses rather than counting lines. Every generated draft puts numbered steps
    inside a quoted cell, so a line count reports a three-case draft as eleven cases --
    a number the reviewer would use to decide whether they have time to review it.
    Unreadable drafts report 0 rather than failing: a broken file must not take the whole
    listing down with it, and `approve` will report the real problem.
    """
    draft = directory / DRAFT_FILENAME
    if not draft.exists():
        return 0
    try:
        from jira_testgen.draft.csv_io import count_draft_rows

        return count_draft_rows(draft)
    except Exception:
        logger.debug("Could not count rows in %s", draft, exc_info=True)
        return 0


def resolve_run(workspace: Path, run_id: str | None) -> RunSummary:
    """Pick the run to act on, or refuse to guess (FR-017a).

    With several pending runs and no id, listing the candidates is the honest answer --
    silently picking one risks publishing the wrong draft to a shared project.
    """
    runs = list_runs(workspace)

    if run_id:
        for run in runs:
            if run.run_id == run_id:
                return run
        known = ", ".join(r.run_id for r in runs) or "none"
        raise NoPendingDraft(
            f"No run named {run_id!r} in {workspace}.",
            f"Known runs: {known}. Run `jira-testgen drafts` for the full listing.",
        )

    pending = [r for r in runs if r.is_pending]
    if not pending:
        raise NoPendingDraft(
            f"No drafts are awaiting review in {workspace}.",
            "Run `jira-testgen generate <ISSUE-KEY>` to create one.",
        )
    if len(pending) > 1:
        listing = "\n    ".join(f"{r.run_id}  ({r.issue_key}, {r.phase.value})" for r in pending)
        raise NoPendingDraft(
            f"{len(pending)} drafts are awaiting review; name the one to act on.",
            f"Re-run with a run id:\n    {listing}",
        )
    return pending[0]
