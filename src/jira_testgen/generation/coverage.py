"""Coverage and shortfall reporting (FR-009, SC-004, SC-005).

A coverage gap that is disclosed in the draft is acceptable under SC-004. A silent one is
not -- the reviewer would approve a set of test cases believing it complete. Everything here
exists to put the gaps in front of them before they approve.
"""

from __future__ import annotations

from jira_testgen.models import SourceRequirement, TestCase

#: SC-005 asks that at least this share of cases be negative or edge scenarios.
NEGATIVE_EDGE_TARGET = 0.30


def uncovered_criteria(source: SourceRequirement, cases: list[TestCase]) -> list[str]:
    """Criterion ids with no test case tracing to them."""
    traced: set[str] = set()
    for case in cases:
        traced.update(case.traces_to)
    return [
        c.criterion_id
        for c in source.criteria
        if c.machine_readable and c.criterion_id not in traced
    ]


def build_coverage_notes(
    source: SourceRequirement,
    cases: list[TestCase],
    *,
    max_cases: int,
    model_notes: list[str] | None = None,
) -> list[str]:
    """Everything the reviewer should know before approving."""
    notes: list[str] = list(model_notes or [])

    uncovered = uncovered_criteria(source, cases)
    if uncovered:
        notes.append(
            f"{len(uncovered)} acceptance criteria have no test case: {', '.join(uncovered)}."
        )

    if source.criteria and not source.has_machine_readable_criteria():
        notes.append(
            "Acceptance criteria were written as prose rather than a list, so per-criterion "
            "coverage could not be traced precisely. Review coverage by hand."
        )
    elif not source.criteria:
        notes.append(
            "No distinct acceptance criteria were found; cases were derived from the "
            "description and trace to REQ."
        )

    if source.unread_node_types:
        notes.append(
            "Non-text content was not read and is not covered: "
            f"{', '.join(source.unread_node_types)}."
        )

    if len(cases) >= max_cases:
        detail = f": {', '.join(uncovered)}" if uncovered else ""
        notes.append(
            f"The per-run limit of {max_cases} test cases was reached, so coverage may be "
            f"incomplete{detail}. Consider splitting the requirement into smaller issues."
        )

    ratio = negative_edge_ratio(cases)
    if ratio < NEGATIVE_EDGE_TARGET:
        notes.append(
            f"Only {ratio:.0%} of cases are negative or edge scenarios, below the {NEGATIVE_EDGE_TARGET:.0%} "
            "target. Consider whether failure paths are under-tested."
        )

    return notes


def negative_edge_ratio(cases: list[TestCase]) -> float:
    if not cases:
        return 0.0
    return sum(c.is_negative_or_edge for c in cases) / len(cases)


def coverage_summary(
    source: SourceRequirement, cases: list[TestCase], max_cases: int
) -> dict[str, object]:
    """Machine-readable counterpart for `--json` output."""
    uncovered = uncovered_criteria(source, cases)
    return {
        "total_cases": len(cases),
        "criteria_total": len(source.criteria),
        "criteria_covered": len(source.criteria) - len(uncovered),
        "criteria_uncovered": uncovered,
        "negative_edge_ratio": round(negative_edge_ratio(cases), 3),
        "cap_reached": len(cases) >= max_cases,
        "unread_content": source.unread_node_types,
    }
