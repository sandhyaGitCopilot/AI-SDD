"""Draft CSV reading and writing (contracts/draft-csv.md, research R6).

CSV was chosen so reviewers can work in a spreadsheet and so drafts interoperate with
existing CSV-based test tooling. The cost is that spreadsheet applications rewrite these
files on save -- re-encoding them, switching the delimiter by locale, re-quoting cells. Every
rule below targets one of those documented failures:

* write ``utf-8-sig``        -- Excel mangles UTF-8 without a BOM
* read BOM-or-not            -- a text editor may strip it
* sniff ``,`` vs ``;``       -- some locales write semicolons
* ``QUOTE_MINIMAL`` via stdlib -- correct RFC 4180 quoting of commas, quotes, newlines
* non-numeric identifiers    -- otherwise Excel coerces them to numbers or dates

The reader is deliberately tolerant. Rejecting a hand-renumbered step list would make the
format hostile to exactly the workflow CSV was chosen for.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jira_testgen.errors import DraftValidationFailure
from jira_testgen.models import Draft, TestCase

logger = logging.getLogger("jira_testgen.draft.csv_io")

COLUMNS = [
    "test_id",
    "approval",
    "kind",
    "summary",
    "preconditions",
    "steps",
    "expected_results",
    "traces_to",
    "jira_key",
    "notes",
]

REQUIRED_COLUMNS = [
    "test_id",
    "approval",
    "kind",
    "summary",
    "steps",
    "expected_results",
    "traces_to",
]

PREAMBLE_PREFIX = "#"
ENCODING_WRITE = "utf-8-sig"
LINE_TERMINATOR = "\r\n"

#: Leading "1. ", "2) ", or "- " on a step line, stripped on read.
_STEP_MARKER = re.compile(r"^\s*(?:\d+\s*[.)\]]|[-*•])\s*")


def encode_steps(items: list[str]) -> str:
    """Numbered lines inside one cell (FR-012b)."""
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, start=1))


def decode_steps(cell: str) -> list[str]:
    """Split a multi-line cell back into steps, tolerating however the user renumbered it.

    Order is positional: the numbers are a reading aid, not the source of truth. A user who
    renumbers inconsistently still gets the order they see on screen.
    """
    if not cell:
        return []
    normalised = cell.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for raw in normalised.split("\n"):
        stripped = _STEP_MARKER.sub("", raw).strip()
        if stripped:
            out.append(stripped)
    return out


def build_preamble(draft: Draft) -> list[str]:
    """Provenance the reviewer needs, as comment lines above the header."""
    source = draft.source
    lines = [
        "jira-testgen draft v1",
        f"run_id: {draft.run_id}",
        f"source_issue: {source.issue_key}",
        f"source_summary: {source.summary}",
        f"fields_read: {', '.join(source.fields_read) or '(none recorded)'}",
        f"generated_by: {draft.generation_service}",
        f"generated_at: {draft.generated_at.isoformat()}",
    ]
    if source.unread_node_types:
        lines.append(f"unread_content: {', '.join(source.unread_node_types)}")
    for note in draft.coverage_notes:
        lines.append(f"coverage_note: {note}")
    lines.append("DO NOT EDIT the test_id column. Edit any other cell freely.")
    return lines


def row_from_case(case: TestCase, jira_key: str = "") -> dict[str, str]:
    return {
        "test_id": case.test_id,
        "approval": case.approval.value,
        "kind": case.case_kind.value,
        "summary": case.summary,
        "preconditions": case.preconditions,
        "steps": encode_steps(case.steps),
        "expected_results": encode_steps(case.expected_results),
        "traces_to": ", ".join(case.traces_to),
        "jira_key": jira_key,
        "notes": case.notes,
    }


def write_draft(path: Path, draft: Draft, jira_keys: dict[str, str] | None = None) -> Path:
    """Write the draft CSV. ``jira_keys`` fills the informational ``jira_key`` column."""
    keys = jira_keys or {}
    path.parent.mkdir(parents=True, exist_ok=True)

    buffer = io.StringIO(newline="")
    for line in build_preamble(draft):
        buffer.write(f"{PREAMBLE_PREFIX} {line}{LINE_TERMINATOR}")

    writer = csv.DictWriter(
        buffer, fieldnames=COLUMNS, lineterminator=LINE_TERMINATOR, quoting=csv.QUOTE_MINIMAL
    )
    writer.writeheader()
    for case in draft.test_cases:
        writer.writerow(row_from_case(case, keys.get(case.test_id, "")))

    path.write_text(buffer.getvalue(), encoding=ENCODING_WRITE, newline="")
    return path


def _read_text(path: Path) -> str:
    """Read as UTF-8 with or without BOM; fall back to cp1252 rather than crashing.

    A spreadsheet on a Windows machine can save CSV in the system codepage. Failing with a
    UnicodeDecodeError would be technically correct and useless to the user.
    """
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _sniff_delimiter(sample: str) -> str:
    """Comma unless the header clearly uses semicolons (a European-locale Excel save)."""
    for line in sample.splitlines():
        if not line.strip() or line.lstrip().startswith(PREAMBLE_PREFIX):
            continue
        return ";" if line.count(";") > line.count(",") else ","
    return ","


def read_preamble(path: Path) -> list[str]:
    text = _read_text(path)
    return [
        line.lstrip(PREAMBLE_PREFIX).strip()
        for line in text.splitlines()
        if line.lstrip().startswith(PREAMBLE_PREFIX)
    ]


@dataclass(frozen=True)
class DraftRecord:
    """One parsed row, plus the line number an editor would show for it.

    The line number is the whole point of this type. FR-016 promises findings the user can
    act on, and "row 3 of the table" is not that when the file on screen has a preamble
    above it and multi-line cells inside it. ``line_number`` is the first *physical* line
    of the record, counting from 1, exactly as a text editor numbers it.
    """

    row: dict[str, str]
    line_number: int


def _split_physical_lines(text: str) -> list[str]:
    """Split exactly where an editor shows a line break.

    ``str.splitlines`` also breaks on form feed, vertical tab and U+2028, none of which a
    text editor counts as a new line. Using it here would silently shift every row number
    after such a character appeared in a cell.
    """
    return re.split(r"\r\n|\r|\n", text)


def read_draft_records(path: Path) -> tuple[list[str], list[DraftRecord]]:
    """Parse the draft into raw rows with their line numbers.

    Returns strings, not models: validation (T045) decides what is acceptable. Keeping
    those apart means a malformed row can be *reported* with its row number instead of
    blowing up the whole parse.
    """
    if not path.exists():
        raise DraftValidationFailure(
            f"Draft file not found at {path}.",
            "Run `jira-testgen drafts` to see which runs exist.",
        )

    text = _read_text(path)
    if not text.strip():
        raise DraftValidationFailure(
            f"The draft at {path} is empty.",
            "Re-generate the draft, or restore the file from your editor's history.",
        )

    delimiter = _sniff_delimiter(text)

    # Keep a map from each body line back to its line number in the file, so a finding can
    # point at the line the user is actually looking at.
    body_lines: list[str] = []
    line_numbers: list[int] = []
    for number, line in enumerate(_split_physical_lines(text), start=1):
        if line.lstrip().startswith(PREAMBLE_PREFIX):
            continue
        body_lines.append(line)
        line_numbers.append(number)

    if not any(ln.strip() for ln in body_lines):
        raise DraftValidationFailure(
            f"The draft at {path} has a preamble but no table.",
            "The header row and test case rows are missing. Re-generate the draft.",
        )

    reader = csv.reader(io.StringIO("\n".join(body_lines)), delimiter=delimiter)
    fieldnames: list[str] = []
    records: list[DraftRecord] = []
    consumed = 0

    try:
        for values in reader:
            start = consumed  # index into line_numbers of this record's first line
            consumed = reader.line_num
            if not fieldnames:
                fieldnames = [v.strip().lstrip("﻿") for v in values]
                continue
            if not any((v or "").strip() for v in values):
                continue
            padded = list(values) + [""] * (len(fieldnames) - len(values))
            records.append(
                DraftRecord(
                    row=dict(zip(fieldnames, padded, strict=False)),
                    line_number=line_numbers[min(start, len(line_numbers) - 1)],
                )
            )
    except csv.Error as exc:
        raise DraftValidationFailure(
            f"The draft at {path} could not be read as CSV: {exc}",
            "A quote or line break is likely unbalanced. Re-open the file and check the last "
            "cell you edited, or re-generate the draft.",
        ) from exc

    missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
    if missing:
        raise DraftValidationFailure(
            f"The draft is missing required column(s): {', '.join(missing)}.",
            f"Restore the header row exactly: {', '.join(COLUMNS)}. Column names are "
            "case-sensitive and must not be renamed.",
        )

    return fieldnames, [
        DraftRecord(row=_normalise_cells(record.row), line_number=record.line_number)
        for record in records
    ]


def _normalise_cells(row: dict[str, str]) -> dict[str, str]:
    """Strip every cell except the multi-line ones, whose internal newlines are content."""
    return {
        key: (value or "") if key in ("steps", "expected_results") else (value or "").strip()
        for key, value in row.items()
        if key is not None
    }


def read_draft_rows(path: Path) -> list[dict[str, str]]:
    """The rows alone, for callers that do not need line numbers."""
    _, records = read_draft_records(path)
    return [record.row for record in records]


def count_draft_rows(path: Path) -> int:
    """How many test cases the draft holds, parsed rather than counted by line.

    Deliberately tolerant where ``read_draft_records`` is strict: this backs the `drafts`
    listing, where the count is informational. Refusing to show a number because a column
    was renamed would hide the run from the listing that exists to help the user find it,
    and `approve` will report the real problem in full.
    """
    text = _read_text(path)
    delimiter = _sniff_delimiter(text)
    body = [
        line
        for line in _split_physical_lines(text)
        if not line.lstrip().startswith(PREAMBLE_PREFIX)
    ]
    reader = csv.reader(io.StringIO("\n".join(body)), delimiter=delimiter)
    try:
        rows = [values for values in reader if any((v or "").strip() for v in values)]
    except csv.Error:
        return 0
    return max(len(rows) - 1, 0)  # minus the header row


def rewrite_draft(
    path: Path,
    rows: list[dict[str, str]],
    preamble: list[str],
    columns: list[str] | None = None,
) -> Path:
    """Rewrite the draft from raw rows, preserving the preamble the file already had.

    Used when the tool must change a cell the user did not -- filling in an identifier for
    a row they added (FR-023a), or writing back ``jira_key`` after publishing. Preserving
    the original preamble rather than regenerating it matters: it carries the provenance of
    the run the user reviewed, and rebuilding it from current state would quietly rewrite
    history.
    """
    buffer = io.StringIO(newline="")
    for line in preamble:
        buffer.write(f"{PREAMBLE_PREFIX} {line}{LINE_TERMINATOR}")

    fieldnames = columns or COLUMNS
    writer = csv.DictWriter(
        buffer,
        fieldnames=fieldnames,
        lineterminator=LINE_TERMINATOR,
        quoting=csv.QUOTE_MINIMAL,
        extrasaction="ignore",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({name: row.get(name, "") for name in fieldnames})

    path.write_text(buffer.getvalue(), encoding=ENCODING_WRITE, newline="")
    return path


def write_back_jira_keys(path: Path, keys: dict[str, str]) -> Path:
    """Fill the ``jira_key`` column after publishing (T065).

    **Informational only.** The duplicate guard reads ``state.json`` and must never read
    this column (research R4, contracts/draft-csv.md). That separation is deliberate: the
    user is invited to edit this file freely, and a reviewer who clears, sorts or copies
    this column cannot thereby cause a duplicate issue or a skipped publish. It exists so
    they can see what was filed without leaving the file they have been working in.

    Every other cell is left exactly as the user left it, and the original preamble is
    preserved rather than regenerated.
    """
    if not keys:
        return path

    fieldnames, records = read_draft_records(path)
    if "jira_key" not in fieldnames:
        # The column was removed. Honour that rather than re-adding it: this value is a
        # convenience, and nothing depends on it.
        logger.info("No jira_key column in %s; skipping write-back.", path)
        return path

    for record in records:
        test_id = record.row.get("test_id", "").strip()
        if test_id in keys:
            record.row["jira_key"] = keys[test_id]

    return rewrite_draft(path, [r.row for r in records], read_preamble(path), fieldnames)


def row_to_payload(row: dict[str, str]) -> dict[str, Any]:
    """Shape one CSV row for ``TestCase`` validation."""
    return {
        "test_id": row.get("test_id", "").strip(),
        "approval": row.get("approval", "pending").strip().lower() or "pending",
        "case_kind": row.get("kind", "positive").strip().lower() or "positive",
        "summary": row.get("summary", "").strip(),
        "preconditions": row.get("preconditions", "").strip(),
        "steps": decode_steps(row.get("steps", "")),
        "expected_results": decode_steps(row.get("expected_results", "")),
        "traces_to": [t.strip() for t in row.get("traces_to", "").split(",") if t.strip()],
        "notes": row.get("notes", "").strip(),
    }
