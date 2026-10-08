"""Atlassian Document Format conversion, both directions (research R1).

Jira Cloud v3 returns body fields as ADF trees and rejects a plain string on write with HTTP
400, so both directions are mandatory -- this is not a convenience layer.

The inbound direction has a second job beyond extracting text: it records every node type it
could not render. The spec's edge case about images, tables, and attachments is really a
question about which node types the extractor understands, and FR-009 requires the unknowns
to be *reported* rather than silently dropped. A node we cannot read is information the
reviewer needs, not an error.
"""

from __future__ import annotations

from typing import Any

#: Node types the extractor renders. Anything else is reported as unread.
SUPPORTED_NODES = frozenset(
    {
        "doc",
        "paragraph",
        "text",
        "heading",
        "bulletList",
        "orderedList",
        "listItem",
        "codeBlock",
        "blockquote",
        "table",
        "tableRow",
        "tableHeader",
        "tableCell",
        "hardBreak",
        "rule",
    }
)

#: Nodes that carry no text but are also not a loss worth reporting.
_SILENT_NODES = frozenset({"hardBreak", "rule"})


class AdfExtraction:
    """Result of walking an ADF tree: the text, plus what could not be read."""

    def __init__(self, text: str, unread_node_types: list[str]) -> None:
        self.text = text
        self.unread_node_types = unread_node_types

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AdfExtraction(text={self.text[:40]!r}..., unread={self.unread_node_types})"


def adf_to_text(node: Any) -> AdfExtraction:
    """Flatten an ADF document to plain text, collecting unreadable node types.

    Accepts a plain string too: some Jira fields are plain text, and callers should not have
    to care which kind they got.
    """
    if node is None:
        return AdfExtraction("", [])
    if isinstance(node, str):
        return AdfExtraction(node.strip(), [])
    if not isinstance(node, dict):
        return AdfExtraction("", [])

    unread: list[str] = []
    lines = _render_node(node, unread, depth=0, list_prefix=None)
    text = "\n".join(line for line in lines).strip()
    # Collapse runs of blank lines left behind by nested block nodes.
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return AdfExtraction(text, sorted(set(unread)))


def _render_node(
    node: dict[str, Any], unread: list[str], depth: int, list_prefix: str | None
) -> list[str]:
    node_type = node.get("type", "")
    content = node.get("content") or []

    if node_type not in SUPPORTED_NODES:
        if node_type and node_type not in _SILENT_NODES:
            unread.append(node_type)
        # Still descend: an unsupported wrapper may hold readable text beneath it.
        return _render_children(content, unread, depth, None)

    if node_type == "text":
        return [str(node.get("text", ""))]

    if node_type == "hardBreak":
        return [""]

    if node_type == "rule":
        return ["---"]

    if node_type == "paragraph":
        inline = "".join(_render_children(content, unread, depth, None))
        prefix = list_prefix or ""
        indent = "  " * depth if depth else ""
        return [f"{indent}{prefix}{inline}".rstrip()] if inline.strip() or prefix else [""]

    if node_type == "heading":
        level = int(node.get("attrs", {}).get("level", 1))
        inline = "".join(_render_children(content, unread, depth, None))
        return ["", f"{'#' * level} {inline}".rstrip(), ""]

    if node_type == "codeBlock":
        inline = "".join(_render_children(content, unread, depth, None))
        return ["```", *inline.split("\n"), "```"]

    if node_type == "blockquote":
        inner = _render_children(content, unread, depth, None)
        return [f"> {line}" if line else ">" for line in inner]

    if node_type in ("bulletList", "orderedList"):
        return _render_list(node, node_type, content, unread, depth)

    if node_type == "listItem":
        return _render_children(content, unread, depth, list_prefix)

    if node_type == "table":
        return _render_table(content, unread, depth)

    # doc, and any supported container not handled above.
    return _render_children(content, unread, depth, None)


def _render_list(
    node: dict[str, Any],
    node_type: str,
    content: list[Any],
    unread: list[str],
    depth: int,
) -> list[str]:
    ordered = node_type == "orderedList"
    start = int(node.get("attrs", {}).get("order", 1)) if ordered else 1
    lines: list[str] = []
    for index, item in enumerate(content):
        if not isinstance(item, dict):
            continue
        prefix = f"{start + index}. " if ordered else "- "
        lines.extend(_render_node(item, unread, depth + 1, prefix))
    return lines


def _render_table(content: list[Any], unread: list[str], depth: int) -> list[str]:
    lines: list[str] = []
    for row in content:
        if not isinstance(row, dict) or row.get("type") != "tableRow":
            continue
        cells: list[str] = []
        for cell in row.get("content") or []:
            if not isinstance(cell, dict):
                continue
            rendered = _render_children(cell.get("content") or [], unread, depth, None)
            cells.append(" ".join(part.strip() for part in rendered if part.strip()))
        if cells:
            lines.append(" | ".join(cells))
    return lines


def _render_children(
    content: list[Any], unread: list[str], depth: int, list_prefix: str | None
) -> list[str]:
    lines: list[str] = []
    for child in content:
        if isinstance(child, dict):
            lines.extend(_render_node(child, unread, depth, list_prefix))
            list_prefix = None  # a prefix applies to the first block only
    return lines


# -- outbound ----------------------------------------------------------


def _paragraph(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _heading(text: str, level: int = 3) -> dict[str, Any]:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [{"type": "text", "text": text}],
    }


def _ordered_list(items: list[str]) -> dict[str, Any]:
    return {
        "type": "orderedList",
        "content": [{"type": "listItem", "content": [_paragraph(item)]} for item in items],
    }


def build_test_case_adf(
    *,
    preconditions: str,
    steps: list[str],
    expected_results: list[str],
    traces_to: list[str] | None = None,
    source_issue_key: str | None = None,
) -> dict[str, Any]:
    """Build the ADF body for a published test case issue (FR-019).

    Laid out so a tester can execute it top to bottom: what must be true, what to do, what
    should happen. When there is one expected result per step they are paired; a single
    overall result is rendered as one paragraph.
    """
    content: list[dict[str, Any]] = []

    if preconditions.strip():
        content.append(_heading("Preconditions"))
        content.append(_paragraph(preconditions.strip()))

    content.append(_heading("Steps"))
    content.append(_ordered_list(steps))

    content.append(_heading("Expected results"))
    if len(expected_results) == 1 and len(steps) != 1:
        content.append(_paragraph(expected_results[0]))
    else:
        content.append(_ordered_list(expected_results))

    if traces_to:
        content.append(_heading("Traceability"))
        trace_text = ", ".join(traces_to)
        if source_issue_key:
            trace_text = f"{source_issue_key} -- {trace_text}"
        content.append(_paragraph(trace_text))

    return {"type": "doc", "version": 1, "content": content}
