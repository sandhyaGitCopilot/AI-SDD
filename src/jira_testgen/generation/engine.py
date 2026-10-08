"""Test case generation against the Claude API (contracts/generation.md, research R7).

Three deliberate choices:

* **Structured outputs**, not prose parsing. The schema makes FR-006's required fields a
  parse-time guarantee instead of a hope.
* **Effort set explicitly to ``high``.** ``claude-opus-5-5`` defaults to ``medium``, one level
  below the rest of the family; deriving negative and edge cases from prose is reasoning work,
  and SC-003/SC-005 are the quality bars that a quieter default would miss.
* **The cap enforced in code**, not only in the prompt. A prompt instruction is a request;
  FR-011 requires a limit that holds.

The client is injected, so no test ever calls the real API (contracts/generation.md).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from pydantic import ValidationError

from jira_testgen.config import GenerationSettings
from jira_testgen.errors import GenerationFailure
from jira_testgen.generation.prompts import SYSTEM_PROMPT, build_user_content
from jira_testgen.models import (
    MAX_TEST_CASES,
    AcceptanceCriterion,
    SourceRequirement,
    TestCase,
)

logger = logging.getLogger("jira_testgen.generation.engine")

MAX_OUTPUT_TOKENS = 32000

TEST_CASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["test_cases", "coverage_notes"],
    "properties": {
        "test_cases": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_TEST_CASES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "summary",
                    "preconditions",
                    "steps",
                    "expected_results",
                    "traces_to",
                    "kind",
                ],
                "properties": {
                    "summary": {"type": "string", "minLength": 1, "maxLength": 255},
                    "preconditions": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "expected_results": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "traces_to": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "kind": {"enum": ["positive", "negative", "edge"]},
                },
            },
        },
        "coverage_notes": {"type": "array", "items": {"type": "string"}},
    },
}


class GenerationClient(Protocol):
    """The slice of the Anthropic SDK this module uses.

    A Protocol rather than the concrete client, so tests inject a stub and the suite stays
    free, fast, and deterministic.
    """

    def generate(self, *, system: str, user_content: str, settings: GenerationSettings) -> str:
        """Return the raw JSON text of the structured response."""
        ...


class AnthropicGenerationClient:
    """Real client. Streams, because a 25-case draft is a long output."""

    def __init__(self, api_key: str) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise GenerationFailure(
                "The anthropic package is not installed.",
                "Install the project dependencies with `pip install -e .`",
            ) from exc
        self._client = Anthropic(api_key=api_key)

    def generate(self, *, system: str, user_content: str, settings: GenerationSettings) -> str:
        from anthropic import (
            APIConnectionError,
            APIStatusError,
            AuthenticationError,
            RateLimitError,
        )
        from anthropic.types import JSONOutputFormatParam, OutputConfigParam

        # Built through the SDK's own TypedDicts so a shape error is a type error here,
        # rather than a 400 at runtime against a real API call.
        output_config: OutputConfigParam = {
            "effort": settings.effort,
            "format": JSONOutputFormatParam(type="json_schema", schema=TEST_CASE_SCHEMA),
        }

        try:
            with self._client.messages.stream(
                model=settings.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                thinking={"type": "adaptive"},
                output_config=output_config,
                messages=[{"role": "user", "content": user_content}],
            ) as stream:
                message = stream.get_final_message()
        except AuthenticationError as exc:
            raise GenerationFailure(
                "The generation service rejected the credentials.",
                "Check ANTHROPIC_API_KEY. This is the AI service, not Jira -- your Jira "
                "credentials are unrelated to this failure.",
            ) from exc
        except RateLimitError as exc:
            raise GenerationFailure(
                "The generation service is rate limiting this account.",
                "Wait a few minutes and re-run. No draft was written, so nothing is lost.",
            ) from exc
        except APIConnectionError as exc:
            raise GenerationFailure(
                f"Could not reach the generation service: {exc}",
                "Check your network connection and any proxy settings, then re-run.",
            ) from exc
        except APIStatusError as exc:
            raise GenerationFailure(
                f"The generation service returned HTTP {exc.status_code}.",
                "If the requirement is very long, the request may exceed the model's limits; "
                "try a narrower issue. Otherwise re-run in a few minutes.",
            ) from exc

        if getattr(message, "stop_reason", None) == "refusal":
            category = getattr(getattr(message, "stop_details", None), "category", None)
            raise GenerationFailure(
                f"The generation service declined this request (category: {category or 'unknown'}).",
                "The requirement content triggered a safety classifier. Review the issue text; "
                "if this looks wrong, generate test cases for a narrower part of it.",
            )

        return _extract_text(message)


def _extract_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "".join(parts)


def parse_generation_payload(
    payload: Any,
    criteria: list[AcceptanceCriterion],
    max_cases: int,
) -> list[TestCase]:
    """Apply checks G1-G6. The schema guarantees shape; this guarantees sense.

    Raises ``GenerationFailure`` (exit 8) naming the check that failed, so the user can tell
    a model problem from a Jira problem (FR-031, SC-008).
    """
    if not isinstance(payload, dict):
        raise GenerationFailure(
            "The generation service returned something that is not a JSON object.",
            "Re-run the command. If it repeats, the requirement may be confusing the model; "
            "try a narrower issue.",
        )

    raw_cases = payload.get("test_cases")
    # G1: an empty result is not a draft.
    if not isinstance(raw_cases, list) or not raw_cases:
        raise GenerationFailure(
            "The generation service returned no test cases.",
            "The requirement may be too vague to derive tests from. Add detail or acceptance "
            "criteria to the Jira issue and re-run.",
        )

    # G4: enforced here, not trusted to the prompt (FR-011).
    if len(raw_cases) > max_cases:
        raise GenerationFailure(
            f"The generation service returned {len(raw_cases)} test cases, over the limit of {max_cases}.",
            f"Re-run the command. The cap of {max_cases} keeps a draft reviewable; it is "
            "enforced locally and will not be silently trimmed.",
        )

    known_ids = {c.criterion_id for c in criteria} | {"REQ"}
    cases: list[TestCase] = []

    for index, raw in enumerate(raw_cases, start=1):
        if not isinstance(raw, dict):
            raise GenerationFailure(
                f"Test case {index} in the generated output is not an object "
                f"(got {type(raw).__name__}).",
                "The generation service returned a malformed case, so no draft was "
                "written. Re-run the command -- this is usually transient. If it "
                "repeats, the requirement may need clearer acceptance criteria.",
            )

        try:
            # G2, G3, G6: one validation path for model output and user edits alike.
            case = TestCase(
                summary=str(raw.get("summary", "")),
                preconditions=str(raw.get("preconditions", "")),
                steps=[str(s) for s in raw.get("steps", [])],
                expected_results=[str(r) for r in raw.get("expected_results", [])],
                traces_to=[str(t) for t in raw.get("traces_to", [])],
                case_kind=raw.get("kind", "positive"),
            )
        except ValidationError as exc:
            detail = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
            )
            raise GenerationFailure(
                f"Test case {index} ({raw.get('summary', 'no summary')!r}) is unusable: {detail}",
                "Re-run the command. If it repeats, the requirement may need clearer "
                "acceptance criteria.",
            ) from exc

        # G5: an unknown trace is worth seeing, not worth discarding the draft over.
        unknown = [t for t in case.traces_to if t not in known_ids]
        if unknown:
            logger.warning("Test case %d traces to unknown criteria: %s", index, ", ".join(unknown))

        cases.append(case)

    return cases


def assign_test_ids(cases: list[TestCase], run_seq: str) -> list[TestCase]:
    """Assign stable identifiers locally (FR-006a, research R6).

    Never taken from model output: the duplicate guard depends on these being deterministic
    and collision-free. The ``TC-`` prefix keeps them non-numeric so no spreadsheet coerces
    them to a number or a date.
    """
    for index, case in enumerate(cases, start=1):
        case.test_id = f"TC-{run_seq}-{index:03d}"
    return cases


def extract_coverage_notes(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    notes = payload.get("coverage_notes")
    if not isinstance(notes, list):
        return []
    return [str(n).strip() for n in notes if str(n).strip()]


def generate_test_cases(
    *,
    source: SourceRequirement,
    settings: GenerationSettings,
    run_seq: str,
    client: GenerationClient,
) -> tuple[list[TestCase], list[str]]:
    """Generate, validate, and identify test cases for one requirement."""
    user_content = build_user_content(source, settings.max_cases)
    logger.info(
        "Requesting up to %d test cases for %s from %s",
        settings.max_cases,
        source.issue_key,
        settings.service_label,
    )

    raw_text = client.generate(system=SYSTEM_PROMPT, user_content=user_content, settings=settings)

    if not raw_text or not raw_text.strip():
        raise GenerationFailure(
            "The generation service returned an empty response.",
            "Re-run the command. Nothing was written, so no draft is left behind.",
        )

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise GenerationFailure(
            f"The generation service returned output that is not valid JSON: {exc}",
            "Re-run the command. If it repeats, the response may have been cut short by the "
            "output limit; try a narrower requirement.",
        ) from exc

    cases = parse_generation_payload(payload, source.criteria, settings.max_cases)
    assign_test_ids(cases, run_seq)
    return cases, extract_coverage_notes(payload)
