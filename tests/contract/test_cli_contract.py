"""T040: the CLI contract (contracts/cli.md).

This file exists to make a breaking change *detectable*. The CLI is this feature's public
interface: a renamed option, a dropped command, or a renumbered exit code breaks every
script built on it, and none of those break a single behavioural test. So the assertions
here are deliberately literal -- they restate the contract table rather than deriving it
from the code, because a test that derives its expectations from the implementation cannot
notice the implementation changing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from jira_testgen import errors
from jira_testgen.cli import app

pytestmark = pytest.mark.contract

runner = CliRunner()


# --- the contract, restated -------------------------------------------------

COMMANDS = {"generate", "approve", "drafts"}

GENERATE_OPTIONS = {
    "--target-project",
    "--ac-field",
    "--issue-type",
    "--link-type",
    "--max-cases",
    "--workspace",
    "--no-wait",
    "--json",
    "--yes",
}

APPROVE_OPTIONS = {"--reject", "--only", "--dry-run", "--workspace", "--json"}

DRAFTS_OPTIONS = {"--workspace", "--json"}

EXIT_CODES = {
    errors.InvalidArguments: 2,
    errors.IssueNotFound: 3,
    errors.IssueForbidden: 4,
    errors.AuthFailure: 5,
    errors.InsufficientContent: 6,
    errors.MissingProjectPermission: 7,
    errors.GenerationFailure: 8,
    errors.NoPendingDraft: 9,
    errors.DraftValidationFailure: 10,
    errors.PublishFailure: 11,
    errors.RetriesExhausted: 12,
    errors.SiteConfigUnresolved: 13,
}


def option_names(command_name: str) -> set[str]:
    command = get_command(app).commands[command_name]  # type: ignore[attr-defined]
    names: set[str] = set()
    for param in command.params:
        names.update(opt for opt in param.opts if opt.startswith("--"))
    return names


def arguments(command_name: str) -> list[Any]:
    """Positional parameters, found by role rather than by class.

    Typer vendors its own click build, so an ``isinstance(p, click.Argument)`` check here
    would assert against whichever click happens to be installed alongside -- not the one
    actually building this CLI. ``param_type_name`` is the stable, public discriminator.
    """
    command = get_command(app).commands[command_name]  # type: ignore[attr-defined]
    return [p for p in command.params if p.param_type_name == "argument"]


def argument_names(command_name: str) -> list[str]:
    return [p.name or "" for p in arguments(command_name)]


class TestCommands:
    def test_all_three_commands_exist(self) -> None:
        assert set(get_command(app).commands) == COMMANDS  # type: ignore[attr-defined]

    @pytest.mark.parametrize("command", sorted(COMMANDS))
    def test_command_has_help(self, command: str) -> None:
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0
        assert command in result.output


class TestGenerateSignature:
    def test_options_match_the_contract(self) -> None:
        assert option_names("generate") >= GENERATE_OPTIONS

    def test_issue_key_is_a_required_argument(self) -> None:
        assert argument_names("generate") == ["issue_key"]
        result = runner.invoke(app, ["generate"])
        assert result.exit_code != 0

    def test_yes_help_text_says_it_bypasses_the_gate(self) -> None:
        """FR-013's gate may be bypassed, but never silently (contracts/cli.md).

        Asserted on the declared help string, not on rendered output: rich wraps and
        truncates to the terminal width, so a width-dependent assertion would be testing
        the console, not the contract.
        """
        command = get_command(app).commands["generate"]  # type: ignore[attr-defined]
        option = next(p for p in command.params if "--yes" in p.opts)
        assert "bypass" in (option.help or "").lower()

    def test_malformed_key_exits_2_with_no_credentials_needed(self) -> None:
        result = runner.invoke(app, ["generate", "not-a-key"])
        assert result.exit_code == errors.InvalidArguments.exit_code

    def test_max_cases_above_25_is_rejected_not_clamped(self) -> None:
        result = runner.invoke(app, ["generate", "PROJ-123", "--max-cases", "99"])
        assert result.exit_code == errors.InvalidArguments.exit_code


class TestApproveSignature:
    def test_options_match_the_contract(self) -> None:
        assert option_names("approve") >= APPROVE_OPTIONS

    def test_run_id_is_an_optional_argument(self) -> None:
        positional = arguments("approve")
        assert [a.name for a in positional] == ["run_id"]
        assert positional[0].required is False

    def test_no_pending_draft_exits_9(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["approve", "--workspace", str(tmp_path / "empty")])
        assert result.exit_code == errors.NoPendingDraft.exit_code


class TestDraftsSignature:
    def test_options_match_the_contract(self) -> None:
        assert option_names("drafts") >= DRAFTS_OPTIONS

    def test_takes_no_arguments(self) -> None:
        assert argument_names("drafts") == []

    def test_empty_listing_exits_0(self, tmp_path: Path) -> None:
        """An empty list is an answer, not a failure (contracts/cli.md)."""
        result = runner.invoke(app, ["drafts", "--workspace", str(tmp_path / "empty")])
        assert result.exit_code == 0

    def test_empty_listing_is_valid_json(self, tmp_path: Path) -> None:
        import json

        result = runner.invoke(app, ["drafts", "--workspace", str(tmp_path / "empty"), "--json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"runs": []}


class TestExitCodes:
    @pytest.mark.parametrize(("error_class", "code"), sorted(EXIT_CODES.items(), key=str))
    def test_each_error_maps_to_its_contract_code(
        self, error_class: type[errors.JiraTestGenError], code: int
    ) -> None:
        assert error_class.exit_code == code

    def test_codes_are_distinct(self) -> None:
        """FR-028: a script must be able to tell outcomes apart."""
        codes = [c.exit_code for c in errors.ERROR_CLASSES]
        assert len(codes) == len(set(codes))

    def test_every_declared_error_is_in_the_contract(self) -> None:
        assert set(errors.ERROR_CLASSES) == set(EXIT_CODES)

    def test_zero_and_one_are_not_reused(self) -> None:
        assert all(c.exit_code > 1 for c in errors.ERROR_CLASSES)
