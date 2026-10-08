"""Reading a Jira requirement (FR-002, FR-004, FR-005, FR-009).

Produces the ``SourceRequirement`` snapshot that the rest of the run works from. Nothing
downstream re-reads Jira: that is what lets an interrupted review resume later without a
network call, and what lets the draft record exactly what it was generated from.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from jira_testgen.errors import InsufficientContent, InvalidArguments
from jira_testgen.jira.adf import adf_to_text
from jira_testgen.jira.client import JiraClient
from jira_testgen.models import (
    ISSUE_KEY_PATTERN,
    AcceptanceCriterion,
    LinkedIssueRef,
    SourceRequirement,
)

logger = logging.getLogger("jira_testgen.jira.reader")

BASE_FIELDS = ["summary", "description", "issuelinks", "issuetype", "project"]

#: Headings that introduce an acceptance criteria block inside a description.
_AC_HEADING = re.compile(
    r"^\s*#*\s*(acceptance\s+criteria|acceptance\s+criterion|ac)\s*:?\s*$",
    re.IGNORECASE,
)

#: A list item: "- x", "* x", "1. x", "1) x", or a Gherkin "Given/When/Then" line.
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+\s*[.)])\s+(?P<text>\S.*)$")
_GHERKIN = re.compile(r"^\s*(given|when|then|and)\b", re.IGNORECASE)

#: Minimum characters before content is considered usable (FR-004).
MIN_USABLE_CONTENT = 20


def validate_issue_key(issue_key: str) -> str:
    """Check the key shape locally, before any network call (contracts/cli.md step 1)."""
    candidate = issue_key.strip().upper()
    if not ISSUE_KEY_PATTERN.match(candidate):
        raise InvalidArguments(
            f"{issue_key!r} is not a Jira issue key.",
            "Use the form PROJECT-123, for example PROJ-456. The project part is uppercase "
            "letters, digits, or underscores.",
        )
    return candidate


def fetch_requirement(
    client: JiraClient,
    issue_key: str,
    *,
    ac_field_id: str | None = None,
    ac_field_name: str | None = None,
) -> SourceRequirement:
    """Fetch the issue and build the frozen snapshot the run works from."""
    key = validate_issue_key(issue_key)
    fields = [*BASE_FIELDS]
    if ac_field_id:
        fields.append(ac_field_id)

    response = client.get(f"/issue/{key}", params={"fields": ",".join(fields)})
    body = response.json_body or {}
    raw_fields: dict[str, Any] = body.get("fields") or {}

    description = adf_to_text(raw_fields.get("description"))
    unread = list(description.unread_node_types)
    fields_read = ["summary", "description"]

    ac_text: str | None = None
    if ac_field_id:
        extracted = adf_to_text(raw_fields.get(ac_field_id))
        if extracted.text.strip():
            ac_text = extracted.text
            fields_read.append(ac_field_name or ac_field_id)
            unread.extend(extracted.unread_node_types)

    # No dedicated field, or it was empty: criteria may still be inside the description.
    if ac_text is None:
        ac_text = extract_criteria_block(description.text)
        if ac_text:
            fields_read.append("description (acceptance criteria section)")

    summary = str(raw_fields.get("summary") or "")
    project_key = str((raw_fields.get("project") or {}).get("key") or key.split("-")[0])

    _require_usable_content(key, summary, description.text, ac_text)

    criteria = parse_criteria(ac_text or description.text, source_field=fields_read[-1])

    source = SourceRequirement(
        issue_key=key,
        summary=summary,
        description_text=description.text,
        acceptance_criteria_text=ac_text,
        criteria=criteria,
        fields_read=fields_read,
        unread_node_types=sorted(set(unread)),
        existing_linked_tests=extract_linked_issues(raw_fields.get("issuelinks") or []),
        project_key=project_key,
    )
    logger.info(
        "Read %s: %d criteria, %d existing links, unread=%s",
        key,
        len(source.criteria),
        len(source.existing_linked_tests),
        source.unread_node_types or "none",
    )
    return source


def _require_usable_content(key: str, summary: str, description: str, ac_text: str | None) -> None:
    """FR-004: stop before spending a model call on a requirement with nothing in it."""
    combined = f"{description or ''}\n{ac_text or ''}".strip()
    if len(combined) >= MIN_USABLE_CONTENT:
        return

    if not combined:
        detail = "has no description and no acceptance criteria"
    else:
        detail = f"has only {len(combined)} characters of content"

    raise InsufficientContent(
        f"{key} ({summary or 'no summary'}) {detail}.",
        "Add a description or acceptance criteria to the Jira issue and re-run. No draft was "
        "written.",
    )


def extract_criteria_block(description: str) -> str | None:
    """Pull an 'Acceptance Criteria' section out of a description, if there is one.

    Stops at the next heading, so a following 'Notes' section is not swallowed.
    """
    if not description:
        return None

    lines = description.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if _AC_HEADING.match(line):
            start = index + 1
            break
    if start is None:
        return None

    collected: list[str] = []
    for line in lines[start:]:
        if line.strip().startswith("#") and collected:
            break
        collected.append(line)

    text = "\n".join(collected).strip()
    return text or None


def parse_criteria(text: str, source_field: str = "") -> list[AcceptanceCriterion]:
    """Split criteria text into discrete, traceable conditions (FR-007).

    List items and Gherkin lines become one criterion each and are marked machine-readable.
    Free prose yields a single criterion marked ``machine_readable=False``, which is what
    drives the SC-004 shortfall disclosure -- the tool says "I could not trace this precisely"
    instead of silently claiming full coverage.
    """
    if not text or not text.strip():
        return []

    lines = [ln for ln in text.splitlines() if ln.strip()]
    items: list[str] = []
    gherkin_buffer: list[str] = []

    for line in lines:
        match = _LIST_ITEM.match(line)
        if match:
            if gherkin_buffer:
                items.append(" ".join(gherkin_buffer))
                gherkin_buffer = []
            items.append(match.group("text").strip())
            continue

        if _GHERKIN.match(line):
            stripped = line.strip()
            if stripped.lower().startswith("given") and gherkin_buffer:
                items.append(" ".join(gherkin_buffer))
                gherkin_buffer = []
            gherkin_buffer.append(stripped)
            continue

        if gherkin_buffer:
            items.append(" ".join(gherkin_buffer))
            gherkin_buffer = []

    if gherkin_buffer:
        items.append(" ".join(gherkin_buffer))

    if items:
        return [
            AcceptanceCriterion(
                criterion_id=f"AC-{i}",
                text=item,
                source_field=source_field,
                machine_readable=True,
            )
            for i, item in enumerate(items, start=1)
        ]

    # Prose: one criterion, flagged as untraceable rather than pretending to be structured.
    condensed = " ".join(ln.strip() for ln in lines)
    return [
        AcceptanceCriterion(
            criterion_id="AC-1",
            text=condensed[:2000],
            source_field=source_field,
            machine_readable=False,
        )
    ]


def extract_linked_issues(issue_links: list[Any]) -> list[LinkedIssueRef]:
    """Issues already linked to the requirement, for the FR-005 report."""
    refs: list[LinkedIssueRef] = []
    for link in issue_links:
        if not isinstance(link, dict):
            continue
        link_type = link.get("type") or {}
        for direction, label_key in (("inwardIssue", "inward"), ("outwardIssue", "outward")):
            issue = link.get(direction)
            if not isinstance(issue, dict):
                continue
            fields = issue.get("fields") or {}
            refs.append(
                LinkedIssueRef(
                    issue_key=str(issue.get("key", "")),
                    summary=str(fields.get("summary") or ""),
                    link_type=str(link_type.get(label_key) or link_type.get("name") or ""),
                    issue_type=str((fields.get("issuetype") or {}).get("name") or ""),
                )
            )
    return [r for r in refs if r.issue_key]
