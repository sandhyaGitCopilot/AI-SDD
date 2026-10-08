"""T069: the exit-code table, and T068's SC-008 audit made permanent.

Two properties are pinned here, both of which are contracts rather than implementation
details, and both of which can be broken silently:

* **Exit codes** (contracts/cli.md). A renumbering breaks every script built on this tool
  and nothing else in the suite would notice -- the behavioural tests assert "the right
  error was raised", not "it still means 3".

* **SC-008**: every user-reachable failure names what went wrong *and* what to do next.
  That was audited once by hand (T068). An audit decays the moment someone adds an error
  with an empty remediation, so it is re-run here over the source on every test run.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from jira_testgen import errors
from jira_testgen.jira.client import AmbiguousWrite, JiraBadRequest

pytestmark = pytest.mark.unit

SRC = pathlib.Path(errors.__file__).parent

#: contracts/cli.md, transcribed. Deliberately a literal copy rather than something
#: derived from the code -- a test that reads its expectations out of the thing it is
#: testing cannot fail.
CONTRACT_EXIT_CODES = {
    "InvalidArguments": 2,
    "IssueNotFound": 3,
    "IssueForbidden": 4,
    "AuthFailure": 5,
    "InsufficientContent": 6,
    "MissingProjectPermission": 7,
    "GenerationFailure": 8,
    "NoPendingDraft": 9,
    "DraftValidationFailure": 10,
    "PublishFailure": 11,
    "RetriesExhausted": 12,
    "SiteConfigUnresolved": 13,
}

#: Errors raised by the Jira client that are not in the top-level table because they are
#: specialisations of one that is. Both must keep the code they specialise.
SPECIALISATIONS = {
    AmbiguousWrite: 11,  # a write of unknown outcome is a publish failure
    JiraBadRequest: 11,  # so is a payload Jira rejects outright
}


class TestExitCodes:
    @pytest.mark.parametrize(("name", "code"), sorted(CONTRACT_EXIT_CODES.items()))
    def test_each_error_has_its_contracted_code(self, name: str, code: int) -> None:
        assert getattr(errors, name).exit_code == code

    def test_no_error_class_is_missing_from_the_contract(self) -> None:
        assert {c.__name__ for c in errors.ERROR_CLASSES} == set(CONTRACT_EXIT_CODES)

    def test_codes_are_distinct(self) -> None:
        """FR-028: a script must be able to tell outcomes apart."""
        codes = [c.exit_code for c in errors.ERROR_CLASSES]
        assert sorted(codes) == sorted(set(codes))

    def test_codes_do_not_collide_with_success_or_internal_error(self) -> None:
        assert all(c.exit_code > 1 for c in errors.ERROR_CLASSES)

    @pytest.mark.parametrize(("error_class", "code"), sorted(SPECIALISATIONS.items(), key=str))
    def test_specialised_errors_keep_the_code_they_specialise(
        self, error_class: type[errors.JiraTestGenError], code: int
    ) -> None:
        assert error_class.exit_code == code
        assert issubclass(error_class, errors.JiraTestGenError)

    def test_the_read_errors_stay_separate(self) -> None:
        """contracts/cli.md is explicit that 3, 4, and 5 must not be merged: a single
        'Jira error' makes a typo in the issue key indistinguishable from an expired
        token, which is exactly the confusion SC-008 exists to prevent."""
        assert (
            len(
                {
                    errors.IssueNotFound.exit_code,
                    errors.IssueForbidden.exit_code,
                    errors.AuthFailure.exit_code,
                }
            )
            == 3
        )


# ---------------------------------------------------------------------------
# T068 -- the SC-008 audit, re-run on every test run
# ---------------------------------------------------------------------------


def _error_constructions() -> list[tuple[str, int, str, str, str]]:
    """Every ``SomeError(message, remediation)`` written anywhere in the package.

    Read from the source rather than by triggering each error: many are reachable only
    through a specific Jira response, and a test that exercised all of them would be a
    test of the mocks, not of the messages.
    """
    names = set(CONTRACT_EXIT_CODES) | {"AmbiguousWrite", "JiraBadRequest"}
    found: list[tuple[str, int, str, str, str]] = []

    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if called not in names or len(node.args) < 2:
                continue
            found.append(
                (
                    path.name,
                    node.lineno,
                    called,
                    _literal_text(node.args[0]),
                    _literal_text(node.args[1]),
                )
            )
    return found


def _literal_text(node: ast.AST) -> str:
    """The literal parts of an expression, so f-strings contribute their fixed text."""
    return " ".join(
        sub.value
        for sub in ast.walk(node)
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    )


#: Verbs that mean the remediation tells the user something to do.
ACTION_WORDS = (
    "run",
    "check",
    "set ",
    "export",
    "ask ",
    "pass ",
    "use ",
    "add ",
    "remove",
    "edit",
    "fix",
    "restore",
    "open",
    "generate",
    "wait",
    "split",
    "adjust",
    "clear",
    "drop ",
    "install",
    "retry",
    "publish",
    "see ",
    "name the",
)

CONSTRUCTIONS = _error_constructions()


class TestEveryErrorMessageMeetsSC008:
    def test_the_audit_actually_found_the_errors(self) -> None:
        """Guards the guard: an AST walk that silently matched nothing would make every
        test below pass by vacuity."""
        assert len(CONSTRUCTIONS) > 40

    @pytest.mark.parametrize("construction", CONSTRUCTIONS, ids=lambda c: f"{c[0]}:{c[1]}")
    def test_it_says_what_went_wrong(self, construction: tuple[str, int, str, str, str]) -> None:
        _, _, _, message, _ = construction
        assert len(message.strip()) >= 15, "the message is too terse to identify"

    @pytest.mark.parametrize("construction", CONSTRUCTIONS, ids=lambda c: f"{c[0]}:{c[1]}")
    def test_it_says_what_to_do_next(self, construction: tuple[str, int, str, str, str]) -> None:
        _, _, _, _, remediation = construction
        assert remediation.strip(), "the remediation is empty"
        assert any(word in remediation.lower() for word in ACTION_WORDS), (
            f"the remediation names no action: {remediation!r}"
        )


class TestRendering:
    def test_render_shows_both_halves(self) -> None:
        error = errors.InvalidArguments("What went wrong.", "What to do next.")
        rendered = error.render()
        assert "What went wrong." in rendered
        assert "What to do next." in rendered

    def test_a_remediation_is_mandatory_at_the_type_level(self) -> None:
        """An error without one is a half-finished message, so the base class refuses to
        let a caller forget."""
        with pytest.raises(TypeError):
            errors.InvalidArguments("only a message")  # type: ignore[call-arg]

    def test_validation_findings_are_rendered_with_the_message(self) -> None:
        error = errors.DraftValidationFailure(
            "The draft failed validation:",
            "Fix the rows and re-approve.",
            findings=["V4 row 7, column kind: 'smoke' is not valid."],
        )
        assert "row 7" in error.render()
