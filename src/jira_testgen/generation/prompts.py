"""Prompt construction for test case generation (contracts/generation.md).

Kept separate from the engine so the wording can be reviewed and tuned without touching
transport, parsing, or validation.
"""

from __future__ import annotations

from jira_testgen.models import SourceRequirement

SYSTEM_PROMPT = """\
You are a senior QA engineer writing manual test cases from a software requirement.

Write test cases a tester can execute without having read the original requirement. Each one
must stand alone: name the concrete data to use, the exact action to take, and the observable
result to check. "Verify it works" is not an expected result; "an error message naming the
invalid field appears beneath the input" is.

Cover more than the happy path. For every requirement, deliberately look for:

- negative cases: invalid input, missing required data, wrong format, unauthorized access,
  actions taken out of order
- edge cases: boundary values (zero, one, maximum, one over maximum), empty and maximum-length
  input, duplicate submissions, interrupted or repeated actions, concurrent use

A set of test cases that only walks the happy path is incomplete work. Aim for at least a
third of the cases to be negative or edge scenarios, where the requirement supports it.

Rules:

- Cover every acceptance criterion you are given with at least one test case, and record which
  criteria each case traces to using the criterion ids provided. Use "REQ" when a case is
  motivated by the description rather than a specific criterion.
- Steps are numbered actions. Expected results are either one per step, in the same order, or
  exactly one result describing the overall outcome. Never any other count.
- Keep each summary under 255 characters and make it specific: it becomes a Jira issue title.
- Classify every case as "positive", "negative", or "edge".
- Do not invent requirements. If something is ambiguous, write the test case for the most
  reasonable reading and note the ambiguity in coverage_notes.
- Use coverage_notes for anything you could not cover and why.
"""


def build_user_content(source: SourceRequirement, max_cases: int) -> str:
    """Frame the requirement, with criteria enumerated so traces_to can reference them."""
    parts: list[str] = [
        f"Issue key: {source.issue_key}",
        f"Summary: {source.summary}",
        "",
        "## Description",
        source.description_text.strip() or "(none provided)",
    ]

    if source.acceptance_criteria_text and source.acceptance_criteria_text.strip():
        parts += ["", "## Acceptance criteria (raw)", source.acceptance_criteria_text.strip()]

    if source.criteria:
        parts += ["", "## Acceptance criteria (enumerated - use these ids in traces_to)"]
        parts += [f"{c.criterion_id}: {c.text}" for c in source.criteria]
    else:
        parts += [
            "",
            "## Acceptance criteria",
            "None were identified as a distinct list. Derive coverage from the description and "
            'trace every case to "REQ". Note the absence in coverage_notes.',
        ]

    if source.unread_node_types:
        # The model should know the picture exists even though it cannot see it -- otherwise
        # it may confidently claim coverage of content nobody read.
        parts += [
            "",
            "## Content that could not be read",
            "The requirement contains non-text content that was not extracted: "
            f"{', '.join(source.unread_node_types)}. Do not assume what it shows. If coverage "
            "depends on it, say so in coverage_notes.",
        ]

    parts += [
        "",
        f"Generate at most {max_cases} test cases. If the requirement has more testable "
        f"conditions than {max_cases} cases can cover, prioritise the highest-risk ones and "
        "list what you left uncovered in coverage_notes.",
    ]
    return "\n".join(parts)
