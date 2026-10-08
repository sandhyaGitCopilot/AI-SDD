"""The human review gate (FR-013, FR-015, FR-018).

This module is the safety property the whole feature is built around. Everything upstream
of it is reversible -- a draft on disk costs nothing and can be deleted. Everything
downstream creates issues in a shared Jira project that someone else will see. FR-013 says
nothing reaches Jira without explicit human approval, and this is where that is enforced.

So the gate is structural, not advisory. ``select_for_publication`` is the only function
that decides what gets published, it returns the empty set unless someone said yes, and
the empty set is a *success* (FR-018) rather than an error -- a reviewer who approves
nothing has used the tool correctly, and a non-zero exit would teach scripted callers to
treat a deliberate decision as a failure.

The prompt itself states what approving does before asking, rather than relying on the
user to have internalised the rule. A gate that is ambiguous at the moment of decision is
not much of a gate.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from jira_testgen.errors import InvalidArguments
from jira_testgen.models import Approval, TestCase

logger = logging.getLogger("jira_testgen.review")


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    ABANDON = "abandon"


#: What the user may type, and what it means. Single letters plus the full words, because
#: a user who types "approve" at a prompt showing "[a]" should not be told they are wrong.
_ANSWERS: dict[str, ReviewAction] = {
    "a": ReviewAction.APPROVE,
    "approve": ReviewAction.APPROVE,
    "y": ReviewAction.APPROVE,
    "yes": ReviewAction.APPROVE,
    "r": ReviewAction.REJECT,
    "reject": ReviewAction.REJECT,
    "n": ReviewAction.REJECT,
    "no": ReviewAction.REJECT,
    "e": ReviewAction.EDIT,
    "edit": ReviewAction.EDIT,
    "o": ReviewAction.EDIT,
    "open": ReviewAction.EDIT,
    "q": ReviewAction.ABANDON,
    "quit": ReviewAction.ABANDON,
    "later": ReviewAction.ABANDON,
}


# ---------------------------------------------------------------------------
# The prompt (T048)
# ---------------------------------------------------------------------------


def prompt_for_review(
    *,
    draft_path: Path,
    case_count: int,
    negative_edge_ratio: float,
    console: Console | None = None,
    ask: Callable[[str], str] | None = None,
) -> ReviewAction:
    """Block until the reviewer decides. Returns what they chose.

    ``ask`` is injectable so the gate can be tested without driving a terminal. It is not
    a way around the gate: every caller that publishes goes through the returned action.
    """
    out = console or Console()
    reader = ask or _default_ask

    out.print(
        Panel(
            f"Draft: [cyan]{draft_path}[/cyan]\n"
            f"Cases: [bold]{case_count}[/bold]  "
            f"Negative or edge: [bold]{negative_edge_ratio:.0%}[/bold]\n\n"
            "[dim]Nothing has been created in Jira. Nothing will be until you approve.[/dim]",
            title="Review required",
            border_style="cyan",
        )
    )

    while True:
        out.print(
            "\n[bold]Approve[/bold] publishes every case except those whose [cyan]approval[/cyan] "
            "column says [red]rejected[/red]."
        )
        answer = reader(
            "  [a]pprove / [r]eject / [e]dit in your editor / [q]uit and decide later: "
        )
        action = _ANSWERS.get(answer.strip().lower())

        if action is None:
            out.print("[yellow]Please answer a, r, e, or q.[/yellow]")
            continue

        if action is ReviewAction.EDIT:
            opened = open_in_editor(draft_path, console=out)
            if opened:
                out.print("[dim]Re-read the draft when you are done, then choose again.[/dim]")
            continue

        return action


def _default_ask(prompt: str) -> str:
    """Read one line. A closed stdin means no human is here to approve.

    Treated as "decide later" rather than as approval: a tool piping input into this
    command has not reviewed anything, and the whole point of FR-013 is that silence is
    never consent.
    """
    if not sys.stdin or not sys.stdin.isatty():
        logger.info("No interactive terminal; treating review as deferred.")
        return "q"
    try:
        return input(prompt)
    except EOFError:
        return "q"


def open_in_editor(path: Path, console: Console | None = None) -> bool:
    """Open the draft in the user's editor. Returns whether it was launched.

    Failing to find an editor is not an error -- the user can open the file themselves,
    and the path is on screen. Turning a convenience into a blocker would be worse than
    not offering it.
    """
    out = console or Console()
    command = os.environ.get("VISUAL") or os.environ.get("EDITOR")

    try:
        if command:
            subprocess.run([*command.split(), str(path)], check=False)
            return True
        # Resolved at runtime rather than through `sys.platform` comparisons, which a type
        # checker narrows to whichever platform it is analysing on -- making the other
        # branches look unreachable and silently unchecked.
        platform = sys.platform
        if platform == "win32":
            startfile = getattr(os, "startfile", None)
            if startfile is not None:
                startfile(str(path))
                return True
        else:
            opener = "open" if platform == "darwin" else "xdg-open"
            if shutil.which(opener):
                subprocess.run([opener, str(path)], check=False)
                return True
    except OSError as exc:
        logger.warning("Could not open an editor: %s", exc)

    out.print(
        f"[yellow]No editor configured.[/yellow] Set $EDITOR, or open the draft yourself:\n  {path}"
    )
    return False


# ---------------------------------------------------------------------------
# Subset approval (T049)
# ---------------------------------------------------------------------------


@dataclass
class Selection:
    """What will be published, and -- just as importantly -- what will not, and why."""

    cases: list[TestCase] = field(default_factory=list)
    skipped_rejected: list[str] = field(default_factory=list)
    skipped_pending: list[str] = field(default_factory=list)
    skipped_not_selected: list[str] = field(default_factory=list)
    basis: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.cases

    @property
    def test_ids(self) -> list[str]:
        return [case.test_id for case in self.cases]

    def describe_skips(self) -> list[str]:
        """Say what was left behind. A silent omission is indistinguishable from a bug."""
        lines = []
        if self.skipped_rejected:
            lines.append(
                f"{len(self.skipped_rejected)} case(s) marked rejected: "
                f"{', '.join(self.skipped_rejected)}"
            )
        if self.skipped_pending:
            lines.append(
                f"{len(self.skipped_pending)} case(s) still pending: "
                f"{', '.join(self.skipped_pending)}"
            )
        if self.skipped_not_selected:
            lines.append(
                f"{len(self.skipped_not_selected)} case(s) not named by --only: "
                f"{', '.join(self.skipped_not_selected)}"
            )
        return lines


def split_only(raw: str) -> list[str]:
    """Split the ``--only`` list. An option given but empty is a mistake, not 'all'."""
    ids = [item.strip() for item in raw.split(",") if item.strip()]
    if not ids:
        raise InvalidArguments(
            "--only was given but names no test ids.",
            "Pass a comma-separated list, e.g. --only TC-7F3A-001,TC-7F3A-004, or omit the "
            "option to publish everything marked approved.",
        )
    return ids


def parse_only(raw: str | None) -> list[str] | None:
    """``split_only``, but tolerating the unset option."""
    return None if raw is None else split_only(raw)


def select_for_publication(
    cases: Sequence[TestCase],
    *,
    only: str | list[str] | None = None,
    approve_all: bool = False,
) -> Selection:
    """Decide what gets published. The one place that decision is made.

    Three modes, in precedence order:

    * ``--only`` -- publish exactly the named cases. Naming a case *is* approving it, which
      is what makes the option usable against a draft the reviewer never edited. A case
      explicitly marked ``rejected`` is still refused, because the file and the command
      line then contradict each other and guessing which one the user meant is not the
      tool's call to make.
    * ``approve_all`` -- the interactive gate and ``--yes``. Everything except cases the
      reviewer marked ``rejected``.
    * otherwise -- the ``approval`` column, which is what a reviewer edits in a spreadsheet.
    """
    by_id = {case.test_id: case for case in cases}
    selection = Selection()

    if only is not None:
        wanted = split_only(only) if isinstance(only, str) else list(only)
        unknown = [test_id for test_id in wanted if test_id not in by_id]
        if unknown:
            raise InvalidArguments(
                f"--only names test id(s) that are not in the draft: {', '.join(unknown)}.",
                f"Known ids: {', '.join(by_id) or 'none'}. Check for a typo, or run "
                "`jira-testgen approve --dry-run` to see the draft's ids.",
            )
        contradicted = [t for t in wanted if by_id[t].approval is Approval.REJECTED]
        if contradicted:
            raise InvalidArguments(
                f"--only names case(s) the draft marks rejected: {', '.join(contradicted)}.",
                "Either clear the `rejected` value in the approval column, or drop those "
                "ids from --only. The tool will not guess which one you meant.",
            )
        selection.basis = "--only"
        selection.cases = [by_id[test_id] for test_id in wanted]
        selection.skipped_not_selected = [t for t in by_id if t not in set(wanted)]
        return selection

    if approve_all:
        selection.basis = "approved at the review prompt"
        for case in cases:
            if case.approval is Approval.REJECTED:
                selection.skipped_rejected.append(case.test_id)
            else:
                selection.cases.append(case)
        return selection

    selection.basis = "the approval column"
    for case in cases:
        if case.approval is Approval.APPROVED:
            selection.cases.append(case)
        elif case.approval is Approval.REJECTED:
            selection.skipped_rejected.append(case.test_id)
        else:
            selection.skipped_pending.append(case.test_id)
    return selection
