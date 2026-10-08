"""Command-line interface (contracts/cli.md).

``cli.py`` stays thin on purpose: it parses, delegates, and renders. Every behavior worth
testing lives in an importable module underneath, so the test suite never has to drive a
terminal to exercise logic.

Exit codes are a contract, not an implementation detail -- FR-028 requires scripted callers
to distinguish outcomes, and SC-008 requires each failure to be identifiable. The
``handle_errors`` decorator is the single place that mapping happens.
"""

from __future__ import annotations

import functools
import json as json_lib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer
from rich.console import Console
from rich.table import Table

from jira_testgen import __version__
from jira_testgen.config import DEFAULT_MAX_CASES, DEFAULT_WORKSPACE
from jira_testgen.errors import JiraTestGenError

app = typer.Typer(
    name="jira-testgen",
    help=(
        "Generate manual test cases from a Jira requirement, review them, and publish them "
        "back as linked issues.\n\n"
        "Requirement text is sent to an external AI service for generation."
    ),
    no_args_is_help=True,
    add_completion=False,
)

stdout = Console()
stderr = Console(stderr=True)

T = TypeVar("T")

EXIT_OK = 0
EXIT_INTERNAL = 1


def handle_errors(func: Callable[..., T]) -> Callable[..., T]:
    """Map a typed error to its exit code, printing the message and the remediation.

    ``typer.Exit`` passes through untouched so commands can exit deliberately.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return func(*args, **kwargs)
        except typer.Exit:
            raise
        except JiraTestGenError as exc:
            stderr.print(f"[red]Error:[/red] {exc.render()}")
            raise typer.Exit(code=exc.exit_code) from exc
        except KeyboardInterrupt:
            stderr.print(
                "\n[yellow]Interrupted.[/yellow] Any draft already written is kept; "
                "run `jira-testgen drafts` to pick it up again."
            )
            raise typer.Exit(code=EXIT_INTERNAL) from None

    return wrapper


def emit_json(payload: dict[str, Any]) -> None:
    """One parseable object on stdout, nothing else (FR-028)."""
    sys.stdout.write(json_lib.dumps(payload, indent=2, default=str) + "\n")


def _version_callback(value: bool) -> None:
    if value:
        stdout.print(f"jira-testgen {__version__}")
        raise typer.Exit(code=EXIT_OK)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Shared entry point."""


# --------------------------------------------------------------------------
# generate
# --------------------------------------------------------------------------


@app.command()
@handle_errors
def generate(
    issue_key: Annotated[str, typer.Argument(help="Source Jira issue key, e.g. PROJ-123.")],
    target_project: Annotated[
        str | None,
        typer.Option(
            "--target-project",
            help="Project to file test cases in. Defaults to the source project.",
        ),
    ] = None,
    ac_field: Annotated[
        str | None,
        typer.Option("--ac-field", help="Name of the acceptance criteria field."),
    ] = None,
    issue_type: Annotated[
        str | None, typer.Option("--issue-type", help="Issue type for created test cases.")
    ] = None,
    link_type: Annotated[
        str | None, typer.Option("--link-type", help="Link relationship name.")
    ] = None,
    max_cases: Annotated[
        int, typer.Option("--max-cases", help="Hard cap on generated test cases (1-25).")
    ] = DEFAULT_MAX_CASES,
    workspace: Annotated[
        Path, typer.Option("--workspace", help="Run directory root.")
    ] = DEFAULT_WORKSPACE,
    no_wait: Annotated[
        bool,
        typer.Option("--no-wait", help="Write the draft and exit instead of waiting for review."),
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit a machine-readable result on stdout.")
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Approve without review. BYPASSES the human review gate; validation still runs.",
        ),
    ] = False,
) -> None:
    """Fetch a Jira requirement, generate test cases, and write a reviewable draft."""
    from jira_testgen.commands.generate import run_generate

    run_generate(
        issue_key=issue_key,
        target_project=target_project,
        ac_field=ac_field,
        issue_type=issue_type,
        link_type=link_type,
        max_cases=max_cases,
        workspace=workspace,
        no_wait=no_wait,
        json_output=json_output,
        yes=yes,
    )


# --------------------------------------------------------------------------
# approve
# --------------------------------------------------------------------------


@app.command()
@handle_errors
def approve(
    run_id: Annotated[
        str | None,
        typer.Argument(help="Which draft to act on. Defaults to the only pending run."),
    ] = None,
    reject: Annotated[
        bool, typer.Option("--reject", help="Mark the draft rejected and create nothing.")
    ] = False,
    only: Annotated[
        str | None, typer.Option("--only", help="Comma-separated test ids to publish.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate and report; touch nothing in Jira.")
    ] = False,
    workspace: Annotated[
        Path, typer.Option("--workspace", help="Run directory root.")
    ] = DEFAULT_WORKSPACE,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit a machine-readable result on stdout.")
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help=(
                "Publish every case except those marked rejected, ignoring the approval "
                "column. For scripted use; validation still runs."
            ),
        ),
    ] = False,
) -> None:
    """Review an existing draft and publish the approved test cases."""
    from jira_testgen.commands.approve import run_approve

    run_approve(
        run_id=run_id,
        reject=reject,
        only=only,
        dry_run=dry_run,
        workspace=workspace,
        json_output=json_output,
        approve_all=yes,
    )


# --------------------------------------------------------------------------
# drafts
# --------------------------------------------------------------------------


@app.command()
@handle_errors
def drafts(
    workspace: Annotated[
        Path, typer.Option("--workspace", help="Run directory root.")
    ] = DEFAULT_WORKSPACE,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit a machine-readable result on stdout.")
    ] = False,
) -> None:
    """List drafts awaiting review."""
    from jira_testgen.draft.state import list_runs

    runs = list_runs(workspace)

    if json_output:
        emit_json(
            {
                "runs": [
                    {
                        "run_id": r.run_id,
                        "issue_key": r.issue_key,
                        "phase": r.phase.value,
                        "cases": r.case_count,
                        "published": r.published_count,
                        "created_at": r.state.created_at,
                        "age_seconds": int(r.age.total_seconds()),
                        "directory": str(r.directory),
                    }
                    for r in runs
                ]
            }
        )
        raise typer.Exit(code=EXIT_OK)

    if not runs:
        # An empty list is an answer, not a failure (contracts/cli.md).
        stdout.print("No runs found. Start one with `jira-testgen generate <ISSUE-KEY>`.")
        raise typer.Exit(code=EXIT_OK)

    table = Table(title="Runs", show_lines=False)
    table.add_column("Run id", style="cyan", no_wrap=True)
    table.add_column("Issue", no_wrap=True)
    table.add_column("Phase")
    table.add_column("Cases", justify="right")
    table.add_column("Published", justify="right")
    table.add_column("Age", no_wrap=True)

    #: Age, not a timestamp, is what the listing is for: a draft from three weeks ago may
    #: describe a requirement that has since changed, and that is the judgement FR-017a
    #: asks the reviewer to make before approving.
    for run in runs:
        table.add_row(
            run.run_id,
            run.issue_key,
            run.phase.value,
            str(run.case_count),
            str(run.published_count),
            run.age_label,
        )

    stdout.print(table)
    pending = sum(1 for r in runs if r.is_pending)
    if pending:
        stdout.print(
            f"[dim]{pending} draft(s) awaiting review. "
            "Run `jira-testgen approve <RUN-ID>` to act on one.[/dim]"
        )
    raise typer.Exit(code=EXIT_OK)


if __name__ == "__main__":  # pragma: no cover
    app()
