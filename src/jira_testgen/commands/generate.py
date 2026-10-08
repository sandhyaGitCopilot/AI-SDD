"""The `generate` command (FR-001 to FR-013, FR-029 to FR-031).

Step order is the contract (contracts/cli.md), and it is ordered by cost: everything that can
fail cheaply fails before anything expensive happens. Key shape is checked with no network
call; site configuration is resolved before a model call is paid for; permissions are checked
before generation so a user is never told about a missing permission *after* spending money.

The last step writes the draft and stops. Nothing in this module writes to Jira.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from jira_testgen.config import configure_run_logging, load_settings
from jira_testgen.draft.csv_io import write_draft
from jira_testgen.draft.state import DRAFT_FILENAME, RunStateStore, build_run_id, run_dir
from jira_testgen.errors import InvalidArguments
from jira_testgen.generation.coverage import build_coverage_notes, coverage_summary
from jira_testgen.generation.engine import (
    AnthropicGenerationClient,
    GenerationClient,
    generate_test_cases,
)
from jira_testgen.jira.client import JiraClient
from jira_testgen.jira.linktypes import resolve_site_config
from jira_testgen.jira.permissions import require_publish_permissions, verify_authentication
from jira_testgen.jira.reader import fetch_requirement, validate_issue_key
from jira_testgen.models import Draft, RunPhase, RunState
from jira_testgen.review import ReviewAction, prompt_for_review

logger = logging.getLogger("jira_testgen.commands.generate")

stdout = Console()
stderr = Console(stderr=True)


def _run_seq() -> str:
    """Four hex characters, so identifiers stay short, unique, and non-numeric-looking."""
    return secrets.token_hex(2).upper()


def run_generate(
    *,
    issue_key: str,
    target_project: str | None,
    ac_field: str | None,
    issue_type: str | None,
    link_type: str | None,
    max_cases: int,
    workspace: Path,
    no_wait: bool,
    json_output: bool,
    yes: bool,
    generation_client: GenerationClient | None = None,
    publisher: Any | None = None,
) -> dict[str, Any]:
    """Execute the generate flow. Returns the result payload (also used for --json)."""
    # 1. Local validation first -- a typo must not cost a network round trip (exit 2).
    key = validate_issue_key(issue_key)

    if no_wait and yes:
        # "Stop before review" and "approve without review" are opposite instructions.
        # Picking one silently would mean either publishing when the user asked to stop,
        # or not publishing when they asked to go ahead -- and the first of those cannot
        # be undone.
        raise InvalidArguments(
            "--no-wait and --yes contradict each other.",
            "--no-wait writes the draft and stops; --yes publishes it without review. "
            "Pass one or the other.",
        )

    settings = load_settings(
        target_project=target_project,
        ac_field=ac_field,
        issue_type=issue_type,
        link_type=link_type,
        max_cases=max_cases,
        workspace=workspace,
    )

    with JiraClient(settings.jira) as client:
        # 1a. Confirm the credentials work before anything interprets a result (exit 5).
        # Jira answers several read endpoints anonymously when a token is wrong -- 200 with
        # an empty body rather than 401 -- so without this probe a bad token surfaces as
        # "this site has no link type named 'Relates'" (exit 13). See verify_authentication.
        verify_authentication(client)

        # 2. Resolve site-specific names before anything expensive (exit 13).
        probe_project = settings.site.target_project or key.split("-")[0]
        site = resolve_site_config(
            client,
            project_key=probe_project,
            link_type_name=settings.site.link_type_name,
            issue_type_name=settings.site.issue_type_name,
            ac_field_name=settings.site.ac_field_name,
        )

        # 3-4. Fetch the issue; refuse an empty requirement (exits 3/4/5, then 6).
        source = fetch_requirement(
            client,
            key,
            ac_field_id=site.ac_field_id,
            ac_field_name=site.ac_field_name,
        )

        resolved_target = settings.site.target_project or source.project_key

        # 5. Show what is already linked, before generating anything (FR-005).
        if not json_output:
            _report_existing_links(source.existing_linked_tests)

        # 6. Permission gate -- before the model call, so a blocked run costs nothing (exit 7).
        require_publish_permissions(client, resolved_target)

    # 7. Generate (exit 8 on any failure; no partial draft is written).
    run_id = build_run_id(key)
    directory = run_dir(workspace, run_id)
    configure_run_logging(directory, list(settings.secrets()))

    client_impl = generation_client or AnthropicGenerationClient(
        settings.generation.api_key.reveal()
    )

    if not json_output:
        stdout.print(
            f"Generating up to {settings.generation.max_cases} test cases for "
            f"[cyan]{key}[/cyan] using {settings.generation.service_label}..."
        )

    cases, model_notes = generate_test_cases(
        source=source,
        settings=settings.generation,
        run_seq=_run_seq(),
        client=client_impl,
    )

    notes = build_coverage_notes(
        source, cases, max_cases=settings.generation.max_cases, model_notes=model_notes
    )

    # 8. Write the draft and the authoritative run state.
    draft = Draft(
        run_id=run_id,
        source=source,
        test_cases=cases,
        coverage_notes=notes,
        generation_service=settings.generation.service_label,
    )
    draft_path = write_draft(directory / DRAFT_FILENAME, draft)

    state = RunState(
        run_id=run_id,
        phase=RunPhase.DRAFTED,
        source_snapshot=source,
        draft_path=str(draft_path),
        target_project_key=resolved_target,
        issue_type_id=site.issue_type.id,
        issue_type_name=site.issue_type.name,
        link_type_name=site.link_type.name,
        generation_meta={
            "service": settings.generation.service_label,
            "model": settings.generation.model,
            "effort": settings.generation.effort,
            "max_cases": settings.generation.max_cases,
        },
    )
    RunStateStore(directory).save(state)

    payload: dict[str, Any] = {
        "run_id": run_id,
        "issue_key": key,
        "draft_path": str(draft_path),
        "run_directory": str(directory),
        "target_project": resolved_target,
        # FR-005 for scripted callers (T091). The human path prints this before generating;
        # without it here, a --json caller is the only one never told the requirement
        # already has test cases linked from a previous run -- which is the audience
        # FR-028 exists for.
        "existing_linked_tests": [ref.model_dump() for ref in source.existing_linked_tests],
        "cases": len(cases),
        "coverage": coverage_summary(source, cases, settings.generation.max_cases),
        "coverage_notes": notes,
        "generated_by": settings.generation.service_label,
        "published": False,
    }

    if not json_output:
        _report_draft(draft, draft_path, notes)

    # 9. The review gate (FR-013). This is the default: generating and publishing in one
    # breath is exactly what the gate exists to prevent, so the command blocks here unless
    # the user has explicitly opted out.
    if yes:
        # --yes bypasses the *review*, not validation. An unparseable or empty draft still
        # fails, and the bypass is announced rather than silent (contracts/cli.md). It is
        # handled before --no-wait and before the JSON exit, because it is an explicit
        # instruction to publish and neither of those may quietly cancel it.
        stderr.print(
            "\n[yellow]--yes: publishing without review.[/yellow] "
            "The draft was not seen by a human before reaching Jira."
        )
        return _handoff(
            run_id=run_id,
            workspace=workspace,
            json_output=json_output,
            approve_all=True,
            publisher=publisher,
        )

    if json_output:
        # Without --yes there is nobody to prompt: the review gate writes to stdout, and
        # FR-028 requires stdout to hold one parseable object. So --json stops at the
        # draft, exactly like --no-wait, and says so in the payload rather than appearing
        # to have published.
        from jira_testgen.cli import emit_json

        payload["awaiting_review"] = True
        payload["next_command"] = f"jira-testgen approve {run_id}"
        emit_json(payload)
        raise typer.Exit(code=0)

    if no_wait:
        stdout.print(
            "\n[dim]Stopped before review (--no-wait). Run "
            f"`jira-testgen approve {run_id}` when you have reviewed the draft.[/dim]"
        )
        raise typer.Exit(code=0)

    action = prompt_for_review(
        draft_path=draft_path,
        case_count=len(draft.test_cases),
        negative_edge_ratio=draft.negative_edge_ratio,
        console=stdout,
    )

    if action is ReviewAction.REJECT:
        return _handoff(
            run_id=run_id,
            workspace=workspace,
            json_output=json_output,
            reject=True,
            publisher=publisher,
        )

    if action is ReviewAction.ABANDON:
        stdout.print(
            "\n[yellow]Left for later.[/yellow] Nothing was created in Jira.\n"
            f"Pick it up with `jira-testgen approve {run_id}`, or list every pending "
            "draft with `jira-testgen drafts`."
        )
        raise typer.Exit(code=0)

    return _handoff(
        run_id=run_id,
        workspace=workspace,
        json_output=json_output,
        approve_all=True,
        publisher=publisher,
    )


def _handoff(
    *,
    run_id: str,
    workspace: Path,
    json_output: bool,
    approve_all: bool = False,
    reject: bool = False,
    publisher: Any | None = None,
) -> dict[str, Any]:
    """Continue into the publish sequence by the same path `approve` takes.

    Deliberately re-reads and re-validates the draft from disk rather than publishing the
    in-memory cases. The reviewer may have edited the file in another window while the
    prompt was waiting, and the file is what they looked at (FR-014).
    """
    from jira_testgen.commands.approve import run_approve

    return run_approve(
        run_id=run_id,
        reject=reject,
        only=None,
        dry_run=False,
        workspace=workspace,
        json_output=json_output,
        approve_all=approve_all,
        publisher=publisher,
    )


def _report_existing_links(links: list[Any]) -> None:
    """FR-005: surface prior test cases before generating more."""
    if not links:
        return
    stdout.print(f"\n[yellow]{len(links)} issue(s) already linked to this requirement:[/yellow]")
    for ref in links[:10]:
        label = f"  {ref.issue_key}"
        if ref.issue_type:
            label += f" ({ref.issue_type})"
        if ref.summary:
            label += f" - {ref.summary}"
        stdout.print(label)
    if len(links) > 10:
        stdout.print(f"  ... and {len(links) - 10} more")
    stdout.print("[dim]Check whether test cases already exist before approving new ones.[/dim]")


def _report_draft(draft: Draft, draft_path: Path, notes: list[str]) -> None:
    table = Table(title=f"Draft for {draft.source.issue_key}", show_lines=False)
    table.add_column("Test id", style="cyan", no_wrap=True)
    table.add_column("Kind", no_wrap=True)
    table.add_column("Summary")
    table.add_column("Traces to", no_wrap=True)

    kind_colour = {"positive": "green", "negative": "red", "edge": "yellow"}
    for case in draft.test_cases:
        colour = kind_colour.get(case.case_kind.value, "white")
        table.add_row(
            case.test_id,
            f"[{colour}]{case.case_kind.value}[/{colour}]",
            case.summary,
            ", ".join(case.traces_to),
        )

    stdout.print(table)
    stdout.print(
        f"{len(draft.test_cases)} cases, {draft.negative_edge_ratio:.0%} negative or edge."
    )

    if notes:
        body = "\n".join(f"- {note}" for note in notes)
        stdout.print(Panel(body, title="Coverage notes", border_style="yellow"))

    stdout.print(f"\nDraft written to [cyan]{draft_path}[/cyan]")
    stdout.print("[dim]Nothing has been created in Jira.[/dim]")
