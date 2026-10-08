"""The review gate and subset approval (T048, T049; FR-013, FR-015, FR-018).

``review.py`` claims that no Jira write is reachable without passing through it. A claim
like that is worth exactly as much as its tests, so this file checks the two halves
separately: the prompt never converts silence or confusion into approval, and
``select_for_publication`` never returns a case nobody said yes to.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jira_testgen.errors import InvalidArguments
from jira_testgen.models import Approval, CaseKind, TestCase
from jira_testgen.review import (
    ReviewAction,
    Selection,
    parse_only,
    prompt_for_review,
    select_for_publication,
)

pytestmark = pytest.mark.unit


def case(index: int, approval: str = "pending", kind: str = "positive") -> TestCase:
    return TestCase(
        test_id=f"TC-AB12-{index:03d}",
        summary=f"Case {index}",
        steps=["Do the thing"],
        expected_results=["It happened"],
        traces_to=["AC-1"],
        approval=Approval(approval),
        case_kind=CaseKind(kind),
    )


class Replies:
    """Scripted answers for the prompt, so the gate can be driven without a terminal."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError("the prompt asked more times than the test scripted")
        return self.answers.pop(0)


def ask(tmp_path: Path, *answers: str, **kwargs: Any) -> tuple[ReviewAction, Replies]:
    replies = Replies(*answers)
    action = prompt_for_review(
        draft_path=tmp_path / "testcases.csv",
        case_count=4,
        negative_edge_ratio=0.5,
        ask=replies,
        **kwargs,
    )
    return action, replies


# ---------------------------------------------------------------------------
# T048 -- the prompt
# ---------------------------------------------------------------------------


class TestPrompt:
    @pytest.mark.parametrize("answer", ["a", "A", "approve", "y", "yes", "  a  "])
    def test_approval_words(self, tmp_path: Path, answer: str) -> None:
        action, _ = ask(tmp_path, answer)
        assert action is ReviewAction.APPROVE

    @pytest.mark.parametrize("answer", ["r", "reject", "n", "no"])
    def test_rejection_words(self, tmp_path: Path, answer: str) -> None:
        action, _ = ask(tmp_path, answer)
        assert action is ReviewAction.REJECT

    @pytest.mark.parametrize("answer", ["q", "quit", "later"])
    def test_abandon_words(self, tmp_path: Path, answer: str) -> None:
        action, _ = ask(tmp_path, answer)
        assert action is ReviewAction.ABANDON

    def test_an_unrecognised_answer_re_asks(self, tmp_path: Path) -> None:
        """It must not fall through to a default. Silence is not consent, and neither is
        a typo."""
        action, replies = ask(tmp_path, "maybe", "what?", "r")
        assert action is ReviewAction.REJECT
        assert len(replies.prompts) == 3

    def test_an_empty_answer_re_asks(self, tmp_path: Path) -> None:
        action, replies = ask(tmp_path, "", "r")
        assert action is ReviewAction.REJECT
        assert len(replies.prompts) == 2

    def test_edit_re_asks_rather_than_deciding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import jira_testgen.review as review

        opened: list[Path] = []
        monkeypatch.setattr(
            review, "open_in_editor", lambda path, console=None: opened.append(path) or True
        )
        action, replies = ask(tmp_path, "e", "a")
        assert action is ReviewAction.APPROVE
        assert opened == [tmp_path / "testcases.csv"]
        assert len(replies.prompts) == 2

    def test_the_prompt_states_what_approving_does(self, tmp_path: Path) -> None:
        """A gate that is ambiguous at the moment of decision is not much of a gate."""
        from io import StringIO

        from rich.console import Console

        buffer = StringIO()
        console = Console(file=buffer, width=100)
        ask(tmp_path, "q", console=console)
        rendered = buffer.getvalue()
        assert "rejected" in rendered
        assert "Nothing has been created in Jira" in rendered

    def test_a_closed_stdin_is_not_approval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A piped invocation has reviewed nothing. Defaulting to approve there would
        turn every CI run into an unreviewed publish."""
        import jira_testgen.review as review

        class NotATerminal:
            @staticmethod
            def isatty() -> bool:
                return False

        monkeypatch.setattr(review.sys, "stdin", NotATerminal())
        assert review._default_ask("? ") == "q"

    def test_eof_is_not_approval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        import jira_testgen.review as review

        class ATerminal:
            @staticmethod
            def isatty() -> bool:
                return True

        monkeypatch.setattr(review.sys, "stdin", ATerminal())

        def raise_eof(prompt: str = "") -> str:
            raise EOFError

        monkeypatch.setattr(builtins, "input", raise_eof)
        assert review._default_ask("? ") == "q"


class TestOpenInEditor:
    def test_a_missing_editor_is_reported_not_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import jira_testgen.review as review

        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.delenv("EDITOR", raising=False)
        monkeypatch.setattr(review.sys, "platform", "linux")
        monkeypatch.setattr(review.shutil, "which", lambda _: None)
        assert review.open_in_editor(tmp_path / "testcases.csv") is False

    def test_the_configured_editor_is_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import jira_testgen.review as review

        launched: list[list[str]] = []
        monkeypatch.setenv("EDITOR", "my-editor --wait")
        monkeypatch.setattr(
            review.subprocess, "run", lambda cmd, **kw: launched.append(cmd) or None
        )
        path = tmp_path / "testcases.csv"
        assert review.open_in_editor(path) is True
        assert launched == [["my-editor", "--wait", str(path)]]


# ---------------------------------------------------------------------------
# T049 -- subset approval
# ---------------------------------------------------------------------------


class TestApprovalColumn:
    def test_only_approved_rows_are_selected(self) -> None:
        cases = [case(1, "approved"), case(2, "pending"), case(3, "rejected")]
        selection = select_for_publication(cases)
        assert selection.test_ids == ["TC-AB12-001"]

    def test_pending_and_rejected_are_reported_separately(self) -> None:
        """The two mean different things, and a reviewer chasing a missing case needs to
        know which one applies to it."""
        cases = [case(1, "approved"), case(2, "pending"), case(3, "rejected")]
        selection = select_for_publication(cases)
        assert selection.skipped_pending == ["TC-AB12-002"]
        assert selection.skipped_rejected == ["TC-AB12-003"]

    def test_nothing_approved_yields_an_empty_selection(self) -> None:
        selection = select_for_publication([case(1), case(2)])
        assert selection.is_empty
        assert selection.cases == []

    def test_an_empty_selection_explains_itself(self) -> None:
        selection = select_for_publication([case(1), case(2, "rejected")])
        described = " ".join(selection.describe_skips())
        assert "pending" in described
        assert "rejected" in described


class TestApproveAll:
    def test_it_takes_everything_not_rejected(self) -> None:
        cases = [case(1, "approved"), case(2, "pending"), case(3, "rejected")]
        selection = select_for_publication(cases, approve_all=True)
        assert selection.test_ids == ["TC-AB12-001", "TC-AB12-002"]

    def test_a_rejected_row_still_wins_over_approve_all(self) -> None:
        """The reviewer marked that row deliberately. Approving at the prompt is a blanket
        yes to what they did not strike out, not an override of what they did."""
        selection = select_for_publication([case(1, "rejected")], approve_all=True)
        assert selection.is_empty
        assert selection.skipped_rejected == ["TC-AB12-001"]


class TestOnly:
    def test_it_selects_exactly_the_named_cases(self) -> None:
        cases = [case(1), case(2), case(3)]
        selection = select_for_publication(cases, only="TC-AB12-001,TC-AB12-003")
        assert selection.test_ids == ["TC-AB12-001", "TC-AB12-003"]

    def test_naming_a_case_approves_it(self) -> None:
        """Otherwise --only would be useless against a draft nobody edited -- every row
        would be pending and nothing would publish."""
        selection = select_for_publication([case(1, "pending")], only="TC-AB12-001")
        assert selection.test_ids == ["TC-AB12-001"]

    def test_whitespace_around_ids_is_tolerated(self) -> None:
        selection = select_for_publication([case(1), case(2)], only=" TC-AB12-001 , TC-AB12-002 ")
        assert len(selection.cases) == 2

    def test_an_unknown_id_is_rejected_with_exit_2(self) -> None:
        with pytest.raises(InvalidArguments) as exc:
            select_for_publication([case(1)], only="TC-AB12-099")
        assert exc.value.exit_code == 2
        assert "TC-AB12-099" in str(exc.value)

    def test_the_known_ids_are_listed_in_the_remediation(self) -> None:
        with pytest.raises(InvalidArguments) as exc:
            select_for_publication([case(1)], only="TC-AB12-099")
        assert "TC-AB12-001" in exc.value.remediation

    def test_naming_a_rejected_case_is_refused_rather_than_guessed(self) -> None:
        """The file says no and the command line says yes. Picking one silently would be
        the tool deciding something only the user can."""
        with pytest.raises(InvalidArguments) as exc:
            select_for_publication([case(1, "rejected")], only="TC-AB12-001")
        assert "rejected" in str(exc.value)

    def test_an_empty_only_list_is_an_error_not_everything(self) -> None:
        with pytest.raises(InvalidArguments):
            select_for_publication([case(1)], only="  ,  ")

    def test_parse_only_returns_none_when_unset(self) -> None:
        assert parse_only(None) is None


class TestSelectionReporting:
    def test_an_empty_selection_has_no_skips_to_describe_when_there_are_none(self) -> None:
        assert Selection().describe_skips() == []

    def test_the_basis_is_recorded(self) -> None:
        """So the end-of-run report can tell the user *why* these cases and not others."""
        assert select_for_publication([case(1)]).basis == "the approval column"
        assert "--only" in select_for_publication([case(1)], only="TC-AB12-001").basis
        assert select_for_publication([case(1)], approve_all=True).basis
