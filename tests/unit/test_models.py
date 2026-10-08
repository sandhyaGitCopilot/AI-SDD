"""T012: every constraint in data-model.md, asserted."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jira_testgen.models import (
    MAX_TEST_CASES,
    AcceptanceCriterion,
    Approval,
    CaseKind,
    Draft,
    PublicationEntry,
    RunState,
    SourceRequirement,
    TestCase,
)

pytestmark = pytest.mark.unit


def make_case(**overrides: object) -> TestCase:
    data: dict[str, object] = {
        "test_id": "TC-7F3A-001",
        "summary": "Password reset rejects an unregistered email",
        "preconditions": "A user account exists.",
        "steps": ["Open the reset page", "Enter an unregistered email", "Submit"],
        "expected_results": ["The page loads", "The field accepts input", "A generic notice shows"],
        "traces_to": ["AC-1"],
        "case_kind": CaseKind.NEGATIVE,
    }
    data.update(overrides)
    return TestCase(**data)  # type: ignore[arg-type]


def make_source(**overrides: object) -> SourceRequirement:
    data: dict[str, object] = {
        "issue_key": "PROJ-123",
        "summary": "Password reset",
        "description_text": "Users can reset their password by email.",
        "project_key": "PROJ",
    }
    data.update(overrides)
    return SourceRequirement(**data)  # type: ignore[arg-type]


class TestSourceRequirement:
    @pytest.mark.parametrize("key", ["PROJ-123", "AB-1", "A1B2_C-99999"])
    def test_accepts_valid_issue_keys(self, key: str) -> None:
        assert make_source(issue_key=key).issue_key == key

    @pytest.mark.parametrize("key", ["proj-123", "PROJ123", "P-123", "PROJ-", "-123", "PROJ-12a"])
    def test_rejects_malformed_issue_keys(self, key: str) -> None:
        with pytest.raises(ValidationError):
            make_source(issue_key=key)

    def test_rejects_empty_description_and_criteria(self) -> None:
        """FR-004: the stop must happen before any generation call."""
        with pytest.raises(ValidationError, match="no description and no acceptance criteria"):
            make_source(description_text="   ", acceptance_criteria_text=None)

    def test_accepts_criteria_only(self) -> None:
        source = make_source(description_text="", acceptance_criteria_text="AC: must work")
        assert source.acceptance_criteria_text == "AC: must work"

    def test_machine_readable_criteria_detection(self) -> None:
        source = make_source(
            criteria=[
                AcceptanceCriterion(criterion_id="AC-1", text="a", machine_readable=False),
            ]
        )
        assert source.has_machine_readable_criteria() is False


class TestTestCaseConstraints:
    def test_blank_test_id_allowed_for_user_added_rows(self) -> None:
        """FR-023a: a hand-added row has no identifier until validation assigns one."""
        assert make_case(test_id="").test_id == ""

    @pytest.mark.parametrize("bad", ["TC-1", "tc-7f3a-001", "TC-7F3A-1", "X-7F3A-001"])
    def test_rejects_malformed_test_id(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            make_case(test_id=bad)

    def test_summary_length_bounds(self) -> None:
        make_case(summary="x" * 255)
        with pytest.raises(ValidationError):
            make_case(summary="x" * 256)
        with pytest.raises(ValidationError):
            make_case(summary="")

    def test_requires_at_least_one_step(self) -> None:
        with pytest.raises(ValidationError):
            make_case(steps=[], expected_results=["ok"])

    def test_whitespace_only_steps_rejected(self) -> None:
        """Schema-valid but semantically empty -- contracts/generation.md G3."""
        with pytest.raises(ValidationError):
            make_case(steps=["   ", ""], expected_results=["ok"])

    def test_steps_are_stripped(self) -> None:
        case = make_case(steps=["  Open page  "], expected_results=["Loads"])
        assert case.steps == ["Open page"]

    def test_expected_results_one_overall_is_valid(self) -> None:
        case = make_case(steps=["a", "b", "c"], expected_results=["All good"])
        assert len(case.expected_results) == 1

    def test_expected_results_one_per_step_is_valid(self) -> None:
        case = make_case(steps=["a", "b"], expected_results=["x", "y"])
        assert len(case.expected_results) == 2

    @pytest.mark.parametrize("results", [["x", "y"], ["x", "y", "z", "w"]])
    def test_expected_results_mismatched_count_rejected(self, results: list[str]) -> None:
        """The rule is exactly 1 or exactly len(steps); 2-of-3 is neither."""
        with pytest.raises(ValidationError, match="exactly 1 entry or exactly 3"):
            make_case(steps=["a", "b", "c"], expected_results=results)

    def test_requires_traces_to(self) -> None:
        with pytest.raises(ValidationError):
            make_case(traces_to=[])

    def test_negative_and_edge_classification(self) -> None:
        assert make_case(case_kind=CaseKind.NEGATIVE).is_negative_or_edge
        assert make_case(case_kind=CaseKind.EDGE).is_negative_or_edge
        assert not make_case(case_kind=CaseKind.POSITIVE).is_negative_or_edge


class TestDraft:
    def _draft(self, cases: list[TestCase]) -> Draft:
        return Draft(run_id="PROJ-123-20261006-120000", source=make_source(), test_cases=cases)

    def test_cap_is_enforced_at_25(self) -> None:
        cases = [make_case(test_id=f"TC-7F3A-{i:03d}") for i in range(1, MAX_TEST_CASES + 1)]
        assert len(self._draft(cases).test_cases) == 25

        too_many = [*cases, make_case(test_id="TC-7F3A-026")]
        with pytest.raises(ValidationError):
            self._draft(too_many)

    def test_rejects_empty_draft(self) -> None:
        with pytest.raises(ValidationError):
            self._draft([])

    def test_duplicate_test_ids_rejected(self) -> None:
        dupes = [make_case(test_id="TC-7F3A-001"), make_case(test_id="TC-7F3A-001")]
        with pytest.raises(ValidationError, match="duplicate test id"):
            self._draft(dupes)

    def test_multiple_blank_ids_allowed(self) -> None:
        """Blank means 'assign me one' -- several blanks are not duplicates."""
        blanks = [make_case(test_id=""), make_case(test_id="")]
        assert len(self._draft(blanks).test_cases) == 2

    def test_negative_edge_ratio(self) -> None:
        cases = [
            make_case(test_id="TC-7F3A-001", case_kind=CaseKind.POSITIVE),
            make_case(test_id="TC-7F3A-002", case_kind=CaseKind.NEGATIVE),
            make_case(test_id="TC-7F3A-003", case_kind=CaseKind.EDGE),
            make_case(test_id="TC-7F3A-004", case_kind=CaseKind.POSITIVE),
        ]
        assert self._draft(cases).negative_edge_ratio == 0.5

    def test_approved_cases_filter(self) -> None:
        cases = [
            make_case(test_id="TC-7F3A-001", approval=Approval.APPROVED),
            make_case(test_id="TC-7F3A-002", approval=Approval.REJECTED),
            make_case(test_id="TC-7F3A-003"),
        ]
        assert [c.test_id for c in self._draft(cases).approved_cases] == ["TC-7F3A-001"]


class TestRunState:
    def _state(self) -> RunState:
        return RunState(
            run_id="PROJ-123-20261006-120000",
            source_snapshot=make_source(),
            draft_path="testcases.csv",
            target_project_key="QA",
        )

    def test_outstanding_skips_published(self) -> None:
        """FR-023: the skip logic that makes re-publishing safe."""
        state = self._state()
        state.publication_record["TC-7F3A-001"] = PublicationEntry(
            test_id="TC-7F3A-001", issue_key="QA-1", linked=True
        )
        outstanding = state.outstanding(["TC-7F3A-001", "TC-7F3A-002"])
        assert outstanding == ["TC-7F3A-002"]

    def test_entry_with_issue_but_no_link_needs_repair(self) -> None:
        """A crash between create and link must be recoverable, not permanent."""
        state = self._state()
        state.publication_record["TC-7F3A-001"] = PublicationEntry(
            test_id="TC-7F3A-001", issue_key="QA-1", linked=False
        )
        repairs = state.entries_needing_link()
        assert [e.test_id for e in repairs] == ["TC-7F3A-001"]
        # It is published, so it must not be created a second time.
        assert state.outstanding(["TC-7F3A-001"]) == []

    def test_entry_without_issue_key_is_not_published(self) -> None:
        entry = PublicationEntry(test_id="TC-7F3A-001", last_error="boom")
        assert entry.is_published is False
        assert entry.needs_link_repair is False
