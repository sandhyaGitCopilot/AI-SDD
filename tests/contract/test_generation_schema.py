"""T021: the generation output contract (contracts/generation.md G1-G6).

The schema guarantees shape. These tests pin the checks that guarantee *sense* -- a payload
can satisfy the JSON schema and still be unusable as a draft.
"""

from __future__ import annotations

from typing import Any

import pytest

from jira_testgen.errors import GenerationFailure
from jira_testgen.generation.engine import TEST_CASE_SCHEMA, parse_generation_payload
from jira_testgen.models import AcceptanceCriterion

pytestmark = pytest.mark.contract

CRITERIA = [
    AcceptanceCriterion(criterion_id="AC-1", text="Reset email is sent"),
    AcceptanceCriterion(criterion_id="AC-2", text="Unregistered email is rejected"),
]


def case(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "summary": "Reset email is sent to a registered address",
        "preconditions": "A registered account exists.",
        "steps": ["Open the reset page", "Enter a registered email", "Submit"],
        "expected_results": ["Page loads", "Field accepts input", "Confirmation is shown"],
        "traces_to": ["AC-1"],
        "kind": "positive",
    }
    data.update(overrides)
    return data


def payload(cases: list[dict[str, Any]], notes: list[str] | None = None) -> dict[str, Any]:
    return {"test_cases": cases, "coverage_notes": notes or []}


class TestSchemaShape:
    """The schema we send to the model must match what contracts/generation.md documents."""

    def test_top_level_requires_cases_and_notes(self) -> None:
        assert set(TEST_CASE_SCHEMA["required"]) == {"test_cases", "coverage_notes"}
        assert TEST_CASE_SCHEMA["additionalProperties"] is False

    def test_case_cap_is_in_the_schema(self) -> None:
        assert TEST_CASE_SCHEMA["properties"]["test_cases"]["maxItems"] == 25
        assert TEST_CASE_SCHEMA["properties"]["test_cases"]["minItems"] == 1

    def test_case_requires_every_structural_field(self) -> None:
        item = TEST_CASE_SCHEMA["properties"]["test_cases"]["items"]
        assert set(item["required"]) == {
            "summary",
            "preconditions",
            "steps",
            "expected_results",
            "traces_to",
            "kind",
        }

    def test_schema_does_not_ask_the_model_for_identifiers(self) -> None:
        """Identifiers are assigned locally -- the duplicate guard must be deterministic."""
        item = TEST_CASE_SCHEMA["properties"]["test_cases"]["items"]
        assert "test_id" not in item["properties"]

    def test_summary_bounds_match_jira(self) -> None:
        props = TEST_CASE_SCHEMA["properties"]["test_cases"]["items"]["properties"]
        assert props["summary"]["maxLength"] == 255
        assert props["summary"]["minLength"] == 1

    def test_kind_is_a_closed_enum(self) -> None:
        props = TEST_CASE_SCHEMA["properties"]["test_cases"]["items"]["properties"]
        assert props["kind"]["enum"] == ["positive", "negative", "edge"]


class TestValidPayloads:
    def test_parses_a_well_formed_payload(self) -> None:
        cases = parse_generation_payload(payload([case()]), CRITERIA, max_cases=25)
        assert len(cases) == 1
        assert cases[0].summary.startswith("Reset email")

    def test_accepts_one_overall_expected_result(self) -> None:
        cases = parse_generation_payload(
            payload([case(steps=["a", "b", "c"], expected_results=["All good"])]),
            CRITERIA,
            max_cases=25,
        )
        assert len(cases[0].expected_results) == 1

    def test_accepts_the_full_cap(self) -> None:
        cases = parse_generation_payload(
            payload([case(summary=f"Case {i}") for i in range(25)]), CRITERIA, max_cases=25
        )
        assert len(cases) == 25


class TestG1EmptyResult:
    def test_no_cases_is_rejected(self) -> None:
        with pytest.raises(GenerationFailure) as exc:
            parse_generation_payload(payload([]), CRITERIA, max_cases=25)
        assert exc.value.exit_code == 8
        assert "no test cases" in exc.value.message.lower()

    def test_missing_key_is_rejected(self) -> None:
        with pytest.raises(GenerationFailure):
            parse_generation_payload({"coverage_notes": []}, CRITERIA, max_cases=25)

    def test_non_dict_payload_is_rejected(self) -> None:
        with pytest.raises(GenerationFailure):
            parse_generation_payload(["not", "a", "dict"], CRITERIA, max_cases=25)  # type: ignore[arg-type]


class TestG2ExpectedResultCount:
    @pytest.mark.parametrize("results", [["x", "y"], ["x", "y", "z", "w"]])
    def test_mismatched_count_is_rejected(self, results: list[str]) -> None:
        with pytest.raises(GenerationFailure) as exc:
            parse_generation_payload(
                payload([case(steps=["a", "b", "c"], expected_results=results)]),
                CRITERIA,
                max_cases=25,
            )
        assert "expected_results" in exc.value.message


class TestG3EmptySteps:
    def test_whitespace_only_steps_rejected(self) -> None:
        """Schema-valid but semantically empty: minLength 1 does not catch a space."""
        with pytest.raises(GenerationFailure):
            parse_generation_payload(
                payload([case(steps=["   ", " "], expected_results=["ok"])]),
                CRITERIA,
                max_cases=25,
            )

    def test_empty_steps_list_rejected(self) -> None:
        with pytest.raises(GenerationFailure):
            parse_generation_payload(
                payload([case(steps=[], expected_results=["ok"])]), CRITERIA, max_cases=25
            )


class TestG4CapEnforcedInCode:
    def test_over_cap_payload_is_rejected_not_truncated(self) -> None:
        """FR-011: the prompt is a request; the code is the constraint."""
        over = [case(summary=f"Case {i}") for i in range(26)]
        with pytest.raises(GenerationFailure) as exc:
            parse_generation_payload(payload(over), CRITERIA, max_cases=25)
        assert "26" in exc.value.message

    def test_respects_a_lower_cap(self) -> None:
        with pytest.raises(GenerationFailure):
            parse_generation_payload(
                payload([case(summary=f"Case {i}") for i in range(6)]), CRITERIA, max_cases=5
            )


class TestG5Traceability:
    def test_unknown_criterion_is_a_warning_not_a_failure(self) -> None:
        """An unknown trace is worth seeing, but not worth throwing the draft away."""
        cases = parse_generation_payload(
            payload([case(traces_to=["AC-99"])]), CRITERIA, max_cases=25
        )
        assert cases[0].traces_to == ["AC-99"]

    def test_req_is_always_a_valid_trace(self) -> None:
        cases = parse_generation_payload(payload([case(traces_to=["REQ"])]), CRITERIA, max_cases=25)
        assert cases[0].traces_to == ["REQ"]

    def test_empty_traces_rejected(self) -> None:
        with pytest.raises(GenerationFailure):
            parse_generation_payload(payload([case(traces_to=[])]), CRITERIA, max_cases=25)


class TestG6ModelRevalidation:
    def test_oversized_summary_rejected(self) -> None:
        with pytest.raises(GenerationFailure):
            parse_generation_payload(payload([case(summary="x" * 256)]), CRITERIA, max_cases=25)

    def test_empty_summary_rejected(self) -> None:
        with pytest.raises(GenerationFailure):
            parse_generation_payload(payload([case(summary="")]), CRITERIA, max_cases=25)

    def test_bad_kind_rejected(self) -> None:
        with pytest.raises(GenerationFailure):
            parse_generation_payload(payload([case(kind="smoke")]), CRITERIA, max_cases=25)

    def test_failure_names_the_offending_case(self) -> None:
        """A rejection the user cannot locate is not actionable (SC-008)."""
        with pytest.raises(GenerationFailure) as exc:
            parse_generation_payload(payload([case(), case(summary="")]), CRITERIA, max_cases=25)
        assert "2" in exc.value.message
        assert exc.value.remediation
