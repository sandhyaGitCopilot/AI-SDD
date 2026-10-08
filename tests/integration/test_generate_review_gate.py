"""T053: the review gate is the default path through `generate` (FR-013).

The gate only means something if it is what happens when the user does nothing special.
A `generate` that quietly behaved like `--no-wait`, or that published on its own, would
satisfy every other test in this suite while breaking the one promise the feature is
built on.

So these tests drive `generate` with no flags and assert on the publisher: it is reached
only after an approval, never before, and never without one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import typer

from jira_testgen.commands.generate import run_generate
from jira_testgen.draft.state import RunStateStore
from jira_testgen.models import RunPhase

from .conftest import default_payload

pytestmark = pytest.mark.integration


def generate(
    tmp_path: Path,
    stub_generation: Any,
    publisher: Any,
    *,
    answers: list[str] | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
    **kwargs: Any,
) -> int:
    """Run `generate` through the gate, scripting the reviewer's answers."""
    if answers is not None:
        assert monkeypatch is not None
        import jira_testgen.commands.generate as generate_module
        import jira_testgen.review as review

        queue = list(answers)
        real_prompt = review.prompt_for_review

        def scripted(**prompt_kwargs: Any) -> Any:
            return real_prompt(**{**prompt_kwargs, "ask": lambda _: queue.pop(0)})

        monkeypatch.setattr(generate_module, "prompt_for_review", scripted)

    options: dict[str, Any] = {
        "issue_key": "PROJ-123",
        "target_project": None,
        "ac_field": None,
        "issue_type": None,
        "link_type": None,
        "max_cases": 25,
        "workspace": tmp_path / ".jira-testgen",
        "no_wait": False,
        "json_output": False,
        "yes": False,
        "generation_client": stub_generation(default_payload(4)),
        "publisher": publisher,
    }
    options.update(kwargs)

    with pytest.raises(typer.Exit) as exc:
        run_generate(**options)
    return int(exc.value.exit_code)


def loaded_state(tmp_path: Path) -> Any:
    runs = list((tmp_path / ".jira-testgen" / "runs").iterdir())
    assert len(runs) == 1
    return RunStateStore(runs[0]).load()


class TestTheGateIsTheDefault:
    def test_approving_at_the_prompt_publishes(
        self,
        tmp_path: Path,
        jira_mock: Any,
        stub_generation: Any,
        stub_publisher: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        publisher = stub_publisher()
        code = generate(
            tmp_path, stub_generation, publisher, answers=["a"], monkeypatch=monkeypatch
        )
        assert code == 0
        assert len(publisher.published) == 4
        assert loaded_state(tmp_path).phase is RunPhase.PUBLISHED

    def test_rejecting_at_the_prompt_publishes_nothing(
        self,
        tmp_path: Path,
        jira_mock: Any,
        stub_generation: Any,
        stub_publisher: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        publisher = stub_publisher()
        code = generate(
            tmp_path, stub_generation, publisher, answers=["r"], monkeypatch=monkeypatch
        )
        assert code == 0
        assert publisher.calls == 0
        assert loaded_state(tmp_path).phase is RunPhase.REJECTED

    def test_walking_away_publishes_nothing_and_leaves_the_draft(
        self,
        tmp_path: Path,
        jira_mock: Any,
        stub_generation: Any,
        stub_publisher: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        publisher = stub_publisher()
        code = generate(
            tmp_path, stub_generation, publisher, answers=["q"], monkeypatch=monkeypatch
        )
        assert code == 0
        assert publisher.calls == 0
        state = loaded_state(tmp_path)
        assert state.phase is RunPhase.DRAFTED
        assert Path(state.draft_path).exists()

    def test_a_typo_at_the_prompt_does_not_publish(
        self,
        tmp_path: Path,
        jira_mock: Any,
        stub_generation: Any,
        stub_publisher: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The re-ask must not fall through to approval while the user is still deciding."""
        publisher = stub_publisher()
        code = generate(
            tmp_path,
            stub_generation,
            publisher,
            answers=["ys", "", "q"],
            monkeypatch=monkeypatch,
        )
        assert code == 0
        assert publisher.calls == 0


class TestNoWait:
    def test_it_stops_before_the_gate_without_publishing(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any, stub_publisher: Any
    ) -> None:
        publisher = stub_publisher()
        code = generate(tmp_path, stub_generation, publisher, no_wait=True)
        assert code == 0
        assert publisher.calls == 0
        assert loaded_state(tmp_path).phase is RunPhase.DRAFTED

    def test_it_never_reaches_the_prompt(
        self,
        tmp_path: Path,
        jira_mock: Any,
        stub_generation: Any,
        stub_publisher: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import jira_testgen.commands.generate as generate_module

        def explode(**kwargs: Any) -> Any:
            raise AssertionError("--no-wait must not block on the prompt")

        monkeypatch.setattr(generate_module, "prompt_for_review", explode)
        assert generate(tmp_path, stub_generation, stub_publisher(), no_wait=True) == 0


class TestYes:
    def test_it_publishes_without_asking(
        self,
        tmp_path: Path,
        jira_mock: Any,
        stub_generation: Any,
        stub_publisher: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import jira_testgen.commands.generate as generate_module

        def explode(**kwargs: Any) -> Any:
            raise AssertionError("--yes must not block on the prompt")

        monkeypatch.setattr(generate_module, "prompt_for_review", explode)
        publisher = stub_publisher()
        assert generate(tmp_path, stub_generation, publisher, yes=True) == 0
        assert len(publisher.published) == 4

    def test_it_still_runs_validation(
        self, tmp_path: Path, jira_mock: Any, stub_generation: Any, stub_publisher: Any
    ) -> None:
        """contracts/cli.md: --yes bypasses the review, not the validator. An unparseable
        draft must still fail rather than reach Jira."""
        from jira_testgen.errors import DraftValidationFailure

        publisher = stub_publisher()
        workspace = tmp_path / ".jira-testgen"

        # Corrupt the draft in the window between writing it and publishing it, which is
        # exactly where an editor left open by the user could do the same thing.
        import jira_testgen.commands.generate as generate_module

        original = generate_module._handoff

        def corrupt_then_handoff(**kwargs: Any) -> Any:
            run_directory = next((workspace / "runs").iterdir())
            (run_directory / "testcases.csv").write_bytes(b"# only a preamble\r\n")
            return original(**kwargs)

        generate_module._handoff = corrupt_then_handoff
        try:
            with pytest.raises(DraftValidationFailure) as exc:
                run_generate(
                    issue_key="PROJ-123",
                    target_project=None,
                    ac_field=None,
                    issue_type=None,
                    link_type=None,
                    max_cases=25,
                    workspace=workspace,
                    no_wait=False,
                    json_output=False,
                    yes=True,
                    generation_client=stub_generation(default_payload(4)),
                    publisher=publisher,
                )
        finally:
            generate_module._handoff = original

        assert exc.value.exit_code == 10
        assert publisher.calls == 0

    def test_the_bypass_is_announced(
        self,
        tmp_path: Path,
        jira_mock: Any,
        stub_generation: Any,
        stub_publisher: Any,
        capsys: Any,
    ) -> None:
        """Bypassing the gate is allowed. Doing it silently is not."""
        generate(tmp_path, stub_generation, stub_publisher(), yes=True)
        assert "--yes" in capsys.readouterr().err


class TestTheGateCannotBeSkipped:
    def test_the_publisher_is_never_reached_before_a_decision(
        self,
        tmp_path: Path,
        jira_mock: Any,
        stub_generation: Any,
        stub_publisher: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The structural claim in review.py, asserted: at the moment the prompt is shown,
        nothing has been published."""
        import jira_testgen.commands.generate as generate_module

        publisher = stub_publisher()
        seen_at_prompt: list[int] = []

        def observe(**kwargs: Any) -> Any:
            seen_at_prompt.append(publisher.calls)
            from jira_testgen.review import ReviewAction

            return ReviewAction.ABANDON

        monkeypatch.setattr(generate_module, "prompt_for_review", observe)
        generate(tmp_path, stub_generation, publisher)
        assert seen_at_prompt == [0]


class TestTheRealPublisher:
    """The gate handing off to the actual writer, not a stub.

    Every other test in this file injects a stub publisher, which proves the gate decides
    correctly but not that the decision reaches Jira. This class closes that gap: it is
    the only place `generate` runs end to end into real HTTP calls.
    """

    def test_approving_at_the_prompt_files_the_issues(
        self,
        tmp_path: Path,
        jira_writes: Any,
        stub_generation: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        code = generate(tmp_path, stub_generation, None, answers=["a"], monkeypatch=monkeypatch)
        assert code == 0
        assert len(jira_writes.created) == 4
        assert len(jira_writes.links) == 4

    def test_yes_files_the_issues_without_a_prompt(
        self, tmp_path: Path, jira_writes: Any, stub_generation: Any
    ) -> None:
        assert generate(tmp_path, stub_generation, None, yes=True) == 0
        assert len(jira_writes.created) == 4

    def test_no_wait_files_nothing(
        self, tmp_path: Path, jira_writes: Any, stub_generation: Any
    ) -> None:
        assert generate(tmp_path, stub_generation, None, no_wait=True) == 0
        assert jira_writes.create_attempts == 0

    def test_rejecting_files_nothing(
        self,
        tmp_path: Path,
        jira_writes: Any,
        stub_generation: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        generate(tmp_path, stub_generation, None, answers=["r"], monkeypatch=monkeypatch)
        assert jira_writes.create_attempts == 0

    def test_the_whole_run_is_recorded_as_published(
        self, tmp_path: Path, jira_writes: Any, stub_generation: Any
    ) -> None:
        generate(tmp_path, stub_generation, None, yes=True)
        state = loaded_state(tmp_path)
        assert state.phase is RunPhase.PUBLISHED
        assert len(state.published_ids()) == 4
        assert state.entries_needing_link() == []
