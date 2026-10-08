"""The `approve` command (FR-014 to FR-024, contracts/cli.md).

This is the resume path, and the order of its steps is the contract:

1. resolve the run, refusing to guess between several (exit 9)
2. **re-read the CSV from disk** -- the user's edits are the input, not whatever was in
   memory when the draft was written (FR-014)
3. validate, and stop at exit 10 without publishing anything (FR-016)
4. select what to publish, where nothing selected is a *success* (FR-018)
5. publish

Two things this module deliberately does not do. It never contacts Jira to re-read the
requirement -- the snapshot in ``state.json`` is the input, which is what makes resuming
free (FR-017, SC-010). And it never generates: a reviewer who walks away and comes back
must get the cases they reviewed, not fresh ones.

Publishing itself lives behind ``Publisher`` rather than inline. That seam is what lets
User Story 2 be tested end to end with no Jira writes at all, which is the only honest way
to assert the FR-013 guarantee.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import typer
from rich.console import Console
from rich.table import Table

from jira_testgen.config import configure_run_logging
from jira_testgen.draft.csv_io import (
    read_draft_records,
    read_preamble,
    rewrite_draft,
    write_back_jira_keys,
)
from jira_testgen.draft.state import RunStateStore, RunSummary, resolve_run
from jira_testgen.draft.validate import ValidationResult, validate_draft
from jira_testgen.errors import PublishFailure
from jira_testgen.models import RunPhase, RunState, TestCase
from jira_testgen.review import Selection, select_for_publication

logger = logging.getLogger("jira_testgen.commands.approve")

stdout = Console()
stderr = Console(stderr=True)

EXIT_OK = 0
EXIT_INTERNAL = 1


@dataclass
class PublishOutcome:
    """What a publishing pass actually did."""

    created: list[dict[str, str]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.failures)


class Publisher(Protocol):
    """Creates and links the issues. Implemented by ``jira/writer.py`` (User Story 3)."""

    def publish(
        self,
        *,
        run: RunSummary,
        cases: list[TestCase],
        state: RunState,
        store: RunStateStore,
        console: Console | None = ...,
    ) -> PublishOutcome: ...


def _known_secrets() -> list[str]:
    """Credential values present in the environment, for the redaction filter.

    Read straight from the environment rather than through ``load_settings`` because
    this runs before the paths that need credentials, and those paths must stay usable
    without them. A value that is absent cannot leak, so an empty list is correct here,
    not a degraded mode.
    """
    import os

    names = ("JIRA_API_TOKEN", "ANTHROPIC_API_KEY")
    return [value for value in (os.environ.get(n, "").strip() for n in names) if value]


def _default_publisher() -> Publisher:
    """The real writer, imported lazily so `approve` loads without touching Jira code."""
    from jira_testgen.jira.writer import JiraPublisher

    return cast("Publisher", JiraPublisher())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_approve(
    *,
    run_id: str | None,
    reject: bool,
    only: str | None,
    dry_run: bool,
    workspace: Path,
    json_output: bool,
    approve_all: bool = False,
    publisher: Publisher | None = None,
) -> dict[str, Any]:
    """Execute the approve flow. Always exits; the payload is returned for `--json`."""
    # 1. Resolve the run. Several pending drafts and no id named is exit 9 with the list,
    #    not a guess -- publishing the wrong draft to a shared project is not recoverable
    #    by re-running (FR-017a).
    run = resolve_run(workspace, run_id)
    store = RunStateStore(run.directory)
    state = store.load()
    draft_path = Path(state.draft_path)

    # Start logging into the run directory before anything can fail. Publishing is the
    # phase most worth a record: a partial publish leaves the user asking which cases
    # reached Jira and why the rest did not, and that question is answered from here
    # (T071). Secrets are resolved only if they are configured -- `approve --dry-run`
    # and `--reject` must keep working with no credentials at all.
    configure_run_logging(run.directory, _known_secrets())
    logger.info(
        "approve run=%s phase=%s reject=%s dry_run=%s only=%s",
        run.run_id,
        state.phase.value,
        reject,
        dry_run,
        only or "-",
    )

    # 2. Rejection happens before validation on purpose. Requiring a valid draft in order
    #    to throw one away would trap the user into fixing a file they have already
    #    decided against (FR-015).
    if reject:
        return _reject(run, state, store, json_output)

    # 3. Re-read from disk. The file is the input, whatever was generated earlier (FR-014).
    result = validate_draft(
        draft_path,
        published=state.publication_record,
        known_criteria={c.criterion_id for c in state.source_snapshot.criteria},
    )
    _report_warnings(result, json_output)
    result.raise_if_fatal()  # exit 10, nothing published, draft untouched

    # 4. Persist identifiers assigned to rows the user added, before anything is published.
    #    Without this the next run would mint different identifiers for the same rows and
    #    publish them a second time -- the exact duplicate SC-007 forbids.
    if result.assigned_ids:
        _persist_assigned_ids(draft_path, result)
        if not json_output:
            stdout.print(
                f"[dim]Assigned {len(result.assigned_ids)} identifier(s) to rows you "
                f"added: {', '.join(result.assigned_ids)}[/dim]"
            )

    # 5. Decide what to publish.
    selection = select_for_publication(result.cases, only=only, approve_all=approve_all)
    already = [t for t in selection.test_ids if t in state.published_ids()]

    if selection.is_empty:
        return _nothing_to_publish(run, state, selection, json_output)

    if dry_run:
        return _dry_run(run, state, selection, already, json_output)

    # 6. Publish.
    return _publish(run, state, store, selection, already, publisher, json_output)


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


def _reject(
    run: RunSummary, state: RunState, store: RunStateStore, json_output: bool
) -> dict[str, Any]:
    store.set_phase(state, RunPhase.REJECTED)
    payload = _payload(run, RunPhase.REJECTED, created=[], failures=[], skipped=[])
    payload["message"] = "Draft rejected. Nothing was created in Jira."

    if json_output:
        _emit(payload)
    else:
        stdout.print(
            f"[yellow]Draft rejected.[/yellow] Nothing was created in Jira.\n"
            f"The draft is still on disk if you want it: [cyan]{state.draft_path}[/cyan]"
        )
    raise typer.Exit(code=EXIT_OK)


def _nothing_to_publish(
    run: RunSummary, state: RunState, selection: Selection, json_output: bool
) -> dict[str, Any]:
    """FR-018: approving nothing is a success. Exit 0, and say why nothing happened."""
    payload = _payload(
        run,
        state.phase,
        created=[],
        failures=[],
        skipped=selection.skipped_rejected + selection.skipped_pending,
    )
    payload["message"] = "Nothing to publish."
    payload["reasons"] = selection.describe_skips()

    if json_output:
        _emit(payload)
    else:
        stdout.print("[yellow]Nothing to publish.[/yellow]")
        for line in selection.describe_skips():
            stdout.print(f"  {line}")
        stdout.print(
            "[dim]Set the approval column to `approved` for the cases you want, then run "
            "`jira-testgen approve` again.[/dim]"
        )
    raise typer.Exit(code=EXIT_OK)


def _dry_run(
    run: RunSummary,
    state: RunState,
    selection: Selection,
    already: list[str],
    json_output: bool,
) -> dict[str, Any]:
    """Report exactly what a real run would do, and touch nothing."""
    outstanding = [c for c in selection.cases if c.test_id not in already]
    payload = _payload(
        run,
        state.phase,
        created=[],
        failures=[],
        skipped=selection.skipped_rejected + selection.skipped_pending,
    )
    payload["dry_run"] = True
    payload["would_create"] = [
        {"test_id": c.test_id, "summary": c.summary, "kind": c.case_kind.value} for c in outstanding
    ]
    payload["already_published"] = already

    if json_output:
        _emit(payload)
        raise typer.Exit(code=EXIT_OK)

    table = Table(title=f"Dry run -- {state.target_project_key}", show_lines=False)
    table.add_column("Test id", style="cyan", no_wrap=True)
    table.add_column("Would create as", no_wrap=True)
    table.add_column("Summary")
    for case in outstanding:
        table.add_row(case.test_id, state.issue_type_name or "issue", case.summary)
    stdout.print(table)

    if already:
        stdout.print(
            f"[dim]{len(already)} case(s) already published and would be skipped: "
            f"{', '.join(already)}[/dim]"
        )
    for line in selection.describe_skips():
        stdout.print(f"[dim]{line}[/dim]")
    stdout.print(
        f"\n{len(outstanding)} issue(s) would be created in "
        f"[cyan]{state.target_project_key}[/cyan] and linked to "
        f"[cyan]{state.source_snapshot.issue_key}[/cyan]."
    )
    stdout.print("[dim]Nothing was created. This was a dry run.[/dim]")
    raise typer.Exit(code=EXIT_OK)


def _publish(
    run: RunSummary,
    state: RunState,
    store: RunStateStore,
    selection: Selection,
    already: list[str],
    publisher: Publisher | None,
    json_output: bool,
) -> dict[str, Any]:
    writer = publisher or _default_publisher()

    store.begin_publishing(state)
    try:
        outcome = writer.publish(
            run=run,
            cases=selection.cases,
            state=state,
            store=store,
            console=None if json_output else stdout,
        )
    except BaseException:
        # The run stays re-runnable: state.json already holds every issue created before
        # the failure, so a second attempt resumes rather than duplicates (research R4).
        # BaseException, not Exception, because a Ctrl-C mid-publish is exactly the case
        # that must not leave the run looking like it was never attempted.
        store.finish_publishing(state, failed=True)
        _write_back_keys(state)
        raise

    store.finish_publishing(state, failed=outcome.failed)
    phase = state.phase

    # Informational only -- the guard above never reads this back (contracts/draft-csv.md).
    _write_back_keys(state)

    payload = _payload(
        run,
        phase,
        created=outcome.created,
        failures=outcome.failures,
        skipped=selection.skipped_rejected + selection.skipped_pending,
    )
    payload["repaired"] = outcome.repaired
    payload["already_published"] = already

    if json_output:
        _emit(payload)
    else:
        _report_outcome(outcome, state, already)

    if outcome.failed:
        raise PublishFailure(
            f"{len(outcome.failures)} of {len(selection.cases)} case(s) could not be published.",
            f"The {len(outcome.created)} issue(s) that were created are recorded. Re-run "
            f"`jira-testgen approve {run.run_id}` to retry only the outstanding cases -- "
            "nothing will be created twice.",
        )

    raise typer.Exit(code=EXIT_OK)


def _write_back_keys(state: RunState) -> None:
    """Fill the draft's ``jira_key`` column (T065). Never fatal -- it is a convenience.

    A failure here must not turn a successful publish into a failed one: the issues
    exist, the authoritative record is in state.json, and the column is only there so
    the reviewer can see the outcome in the file they were working in.
    """
    keys = {
        test_id: entry.issue_key
        for test_id, entry in state.publication_record.items()
        if entry.issue_key
    }
    if not keys:
        return
    try:
        write_back_jira_keys(Path(state.draft_path), keys)
    except Exception:
        logger.warning("Could not write jira_key back into the draft", exc_info=True)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _report_warnings(result: ValidationResult, json_output: bool) -> None:
    if json_output or not result.warnings:
        return
    stdout.print("[yellow]Validation warnings (publishing continues):[/yellow]")
    for finding in result.warnings:
        stdout.print(f"  {finding.render()}")


def _report_outcome(outcome: PublishOutcome, state: RunState, already: list[str]) -> None:
    """FR-024: every created issue with its URL, and every failure with its reason."""
    if outcome.created:
        table = Table(title="Created", show_lines=False)
        table.add_column("Test id", style="cyan", no_wrap=True)
        table.add_column("Issue", no_wrap=True)
        table.add_column("URL")
        for entry in outcome.created:
            table.add_row(entry["test_id"], entry["issue_key"], entry.get("issue_url", ""))
        stdout.print(table)

    if outcome.repaired:
        stdout.print(
            f"Repaired {len(outcome.repaired)} issue(s) that existed but were not "
            f"linked: {', '.join(outcome.repaired)}"
        )

    if outcome.failures:
        if already:
            stdout.print(f"[dim]Skipped {len(already)} already-published case(s).[/dim]")
        stderr.print("[red]Failures:[/red]")
        for entry in outcome.failures:
            stderr.print(f"  {entry['test_id']}: {entry.get('error', 'unknown error')}")
        return

    if not outcome.created and already:
        # Everything was filed on an earlier run. Saying "0 issues linked" here would
        # read like a failure; this is the normal, correct outcome of re-approving.
        stdout.print(
            f"[green]Nothing to do.[/green] All {len(already)} case(s) were already "
            f"published and linked to [cyan]{state.source_snapshot.issue_key}[/cyan]: "
            f"{', '.join(already)}"
        )
        return

    if already:
        stdout.print(f"[dim]Skipped {len(already)} already-published case(s).[/dim]")
    stdout.print(
        f"[green]Done.[/green] {len(outcome.created)} issue(s) linked to "
        f"[cyan]{state.source_snapshot.issue_key}[/cyan]."
    )


def _payload(
    run: RunSummary,
    phase: RunPhase,
    *,
    created: list[dict[str, str]],
    failures: list[dict[str, str]],
    skipped: list[str],
) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "issue_key": run.issue_key,
        "phase": phase.value,
        "created": created,
        "failures": failures,
        "skipped": skipped,
    }


def _emit(payload: dict[str, Any]) -> None:
    from jira_testgen.cli import emit_json

    emit_json(payload)


# ---------------------------------------------------------------------------
# Identifier write-back (T046)
# ---------------------------------------------------------------------------


def _persist_assigned_ids(draft_path: Path, result: ValidationResult) -> None:
    """Write freshly minted identifiers back into the draft, changing nothing else.

    The original preamble is preserved rather than regenerated: it records the run the
    user actually reviewed, and rebuilding it from current state would rewrite history in
    a file whose whole job is to be trustworthy.
    """
    fieldnames, records = read_draft_records(draft_path)
    blanks = [r for r in records if not r.row.get("test_id", "").strip()]
    if len(blanks) != len(result.assigned_ids):
        # Belt and braces: if the file changed under us between validation and write-back,
        # leave it alone rather than writing identifiers against the wrong rows.
        logger.warning("Draft changed during validation; not writing back assigned identifiers.")
        return

    for record, test_id in zip(blanks, result.assigned_ids, strict=True):
        record.row["test_id"] = test_id

    rewrite_draft(
        draft_path,
        [r.row for r in records],
        read_preamble(draft_path),
        columns=fieldnames,
    )
