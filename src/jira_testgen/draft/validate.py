"""Draft validation, rules V1-V11 (contracts/draft-csv.md, FR-016, FR-023a).

Three principles shape this module, and each is a response to something a user actually
does with the draft:

1. **Report every finding, not the first.** A validator that stops at the first bad row
   turns fixing a draft into an N-pass job against a tool that will not tell you how many
   passes remain.

2. **Report by the row number the user's editor shows.** That means counting the preamble
   and the header, and counting the physical lines a multi-line ``steps`` cell occupies.
   "Row 3 of the table" is useless against a file whose first table row is on line 10.

3. **Warnings do not block.** V8 and V9 describe things a reviewer may do deliberately --
   tracing a case they wrote to a criterion the tool never saw, or deleting a row that was
   already published. Refusing to publish over those would be the tool overruling the
   person it exists to serve. They are printed; they do not stop the run.

Identifiers (T046) get special treatment, because ``test_id`` is what the duplicate guard
keys on (research R4):

* **blank** means a row the user added -- it is accepted and assigned a fresh identifier
* **duplicated** is a hard rejection naming *both* rows
* **changed** is indistinguishable from a new case, so it is treated as new, which is
  exactly why the preamble tells the user not to edit the column
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydantic import ValidationError

from jira_testgen.draft.csv_io import (
    DraftRecord,
    decode_steps,
    read_draft_records,
    row_to_payload,
)
from jira_testgen.errors import DraftValidationFailure
from jira_testgen.models import (
    MAX_SUMMARY_LENGTH,
    MAX_TEST_CASES,
    TEST_ID_PATTERN,
    Approval,
    CaseKind,
    Origin,
    PublicationEntry,
    TestCase,
)

#: A criterion reference that always validates -- "this case covers the requirement as a
#: whole" is a legitimate thing for a reviewer to mean.
WHOLE_REQUIREMENT = "REQ"

_RUN_SEQ_FROM_ID = re.compile(r"^TC-([A-Z0-9]{4})-([0-9]{3})$")


class Severity(StrEnum):
    FATAL = "fatal"
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    """One validation result, addressed to a place in the user's file."""

    rule: str
    severity: Severity
    message: str
    row: int | None = None
    column: str | None = None

    @property
    def is_fatal(self) -> bool:
        return self.severity is Severity.FATAL

    def render(self) -> str:
        where = []
        if self.row is not None:
            where.append(f"row {self.row}")
        if self.column:
            where.append(f"column {self.column}")
        location = ", ".join(where)
        prefix = f"{self.rule} {location}: " if location else f"{self.rule}: "
        return f"{prefix}{self.message}"


@dataclass
class ValidationResult:
    """Everything the draft yielded: the cases, and everything wrong with them."""

    cases: list[TestCase] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    assigned_ids: list[str] = field(default_factory=list)

    @property
    def fatal_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.is_fatal]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if not f.is_fatal]

    @property
    def is_valid(self) -> bool:
        return not self.fatal_findings

    def raise_if_fatal(self) -> None:
        """Turn fatal findings into the exit-10 failure the contract promises."""
        fatal = self.fatal_findings
        if not fatal:
            return
        raise DraftValidationFailure(
            f"The draft failed validation with {len(fatal)} error(s):",
            "Fix the rows above and run `jira-testgen approve` again. Nothing has been "
            "created in Jira, and the draft is unchanged on disk.",
            findings=[f.render() for f in fatal],
        )


# ---------------------------------------------------------------------------
# Identifier assignment (T046)
# ---------------------------------------------------------------------------


def _run_seq(existing: list[str]) -> str:
    """Reuse the draft's own run sequence so added rows look like they belong to it."""
    for value in existing:
        match = _RUN_SEQ_FROM_ID.match(value)
        if match:
            return match.group(1)
    return secrets.token_hex(2).upper()


class _IdentifierMinter:
    """Hands out identifiers that have never been used in this run.

    Deliberately never reuses a gap. A number freed by a deleted row may still be in the
    publication record, and handing it to a newly added case would make that case look
    already-published -- the tool would skip it, and the user would silently lose a test.
    """

    def __init__(self, existing: list[str], reserved: set[str]) -> None:
        self.prefix = _run_seq(existing)
        self.taken = set(existing) | reserved
        highest = 0
        for value in self.taken:
            match = _RUN_SEQ_FROM_ID.match(value)
            if match and match.group(1) == self.prefix:
                highest = max(highest, int(match.group(2)))
        self._next = highest + 1

    def mint(self) -> str:
        while True:
            candidate = f"TC-{self.prefix}-{self._next:03d}"
            self._next += 1
            if candidate not in self.taken:
                self.taken.add(candidate)
                return candidate


# ---------------------------------------------------------------------------
# Per-row rules
# ---------------------------------------------------------------------------


def _check_enum(
    raw: str,
    allowed: type[StrEnum],
    *,
    rule: str,
    column: str,
    line: int,
    default: str,
) -> tuple[str, Finding | None]:
    value = (raw or "").strip().lower() or default
    if value not in {member.value for member in allowed}:
        options = ", ".join(sorted(m.value for m in allowed))
        return default, Finding(
            rule=rule,
            severity=Severity.FATAL,
            message=f"{raw.strip()!r} is not a valid {column}. Use one of: {options}.",
            row=line,
            column=column,
        )
    return value, None


def _validate_row(
    record: DraftRecord,
    *,
    known_criteria: set[str] | None,
) -> tuple[dict[str, object] | None, list[Finding]]:
    """Check one row against V3-V8, returning a payload only if it is salvageable."""
    line = record.line_number
    row = record.row
    findings: list[Finding] = []

    payload = row_to_payload(row)

    # V3 / V4 -- enumerated values. Checked here rather than left to the model so the
    # finding can name the column and show the allowed values.
    approval, finding = _check_enum(
        row.get("approval", ""),
        Approval,
        rule="V3",
        column="approval",
        line=line,
        default=Approval.PENDING.value,
    )
    if finding:
        findings.append(finding)
    payload["approval"] = approval

    kind, finding = _check_enum(
        row.get("kind", ""),
        CaseKind,
        rule="V4",
        column="kind",
        line=line,
        default=CaseKind.POSITIVE.value,
    )
    if finding:
        findings.append(finding)
    payload["case_kind"] = kind

    # V5 -- summary
    summary = str(payload["summary"])
    if not summary:
        findings.append(Finding("V5", Severity.FATAL, "summary is empty.", line, "summary"))
    elif len(summary) > MAX_SUMMARY_LENGTH:
        findings.append(
            Finding(
                "V5",
                Severity.FATAL,
                f"summary is {len(summary)} characters; the limit is {MAX_SUMMARY_LENGTH}.",
                line,
                "summary",
            )
        )

    # V6 -- at least one real step
    steps = decode_steps(row.get("steps", ""))
    if not steps:
        findings.append(
            Finding(
                "V6",
                Severity.FATAL,
                "steps contains no step text. Numbering alone is not a step.",
                line,
                "steps",
            )
        )

    # V7 -- expected results count
    results = decode_steps(row.get("expected_results", ""))
    if steps and len(results) not in (1, len(steps)):
        findings.append(
            Finding(
                "V7",
                Severity.FATAL,
                f"expected_results has {len(results)} entr(y/ies); it must have exactly 1 "
                f"(one overall) or exactly {len(steps)} (one per step).",
                line,
                "expected_results",
            )
        )
    elif not steps and not results:
        findings.append(
            Finding(
                "V7",
                Severity.FATAL,
                "expected_results is empty.",
                line,
                "expected_results",
            )
        )

    # V8 -- unknown criterion id. A warning: the reviewer may know something the tool does
    # not, and blocking a publish over a traceability label would be an overreach.
    traces = [str(t) for t in payload["traces_to"]]
    if known_criteria is not None:
        unknown = [t for t in traces if t != WHOLE_REQUIREMENT and t not in known_criteria]
        if unknown:
            findings.append(
                Finding(
                    "V8",
                    Severity.WARNING,
                    f"traces_to references {', '.join(unknown)}, which is not a criterion "
                    f"read from the requirement. Use {WHOLE_REQUIREMENT} for a case that "
                    "covers the requirement as a whole.",
                    line,
                    "traces_to",
                )
            )

    return payload, findings


def _build_case(
    payload: dict[str, object], line: int, already_reported: set[str | None]
) -> tuple[TestCase | None, list[Finding]]:
    """Final pass through the model -- one definition of a valid case, not two.

    ``already_reported`` holds the columns a named V-rule has just reported on this row.
    Those are skipped: pydantic's own wording ("String should have at least 1 character")
    is strictly worse than the V-rule's, and printing both makes the user read two lines
    to learn one thing. The model remains the backstop for constraints the V-table does
    not name, which is the only reason it runs after them.
    """
    try:
        return TestCase.model_validate(payload), []
    except ValidationError as exc:
        findings = []
        for error in exc.errors():
            column = str(error["loc"][0]) if error["loc"] else None
            if column in already_reported:
                continue
            findings.append(Finding("MODEL", Severity.FATAL, error["msg"], line, column))
        return None, findings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def validate_draft(
    path: Path,
    *,
    published: dict[str, PublicationEntry] | None = None,
    known_criteria: set[str] | None = None,
) -> ValidationResult:
    """Validate a draft CSV against V1-V11.

    V1 and V11 raise immediately: a file with no header or no readable table has no rows
    to report findings against, so there is nothing more useful to say than what went
    wrong with the file itself. Everything else is collected.
    """
    # V1 and V11 -- raised by the reader, because a finding needs a row to point at.
    _, records = read_draft_records(path)

    if not records:
        raise DraftValidationFailure(
            f"The draft at {path} has a header but no test case rows.",
            "Re-generate the draft, or restore the rows from your editor's history.",
        )

    result = ValidationResult()

    # V10 -- the cap. Reported as a finding rather than raised, so the user also sees
    # whatever else is wrong and fixes the file once.
    if len(records) > MAX_TEST_CASES:
        result.findings.append(
            Finding(
                "V10",
                Severity.FATAL,
                f"The draft has {len(records)} rows; the limit is {MAX_TEST_CASES}. "
                "A larger draft stops being reviewable, which is the point of the cap.",
                records[MAX_TEST_CASES].line_number,
                "test_id",
            )
        )

    raw_ids = [record.row.get("test_id", "").strip() for record in records]

    # V2 -- duplicates, reported against BOTH rows so the user is not left hunting.
    first_seen: dict[str, int] = {}
    duplicated: set[int] = set()
    for record, value in zip(records, raw_ids, strict=True):
        if not value:
            continue
        if value in first_seen:
            duplicated.add(record.line_number)
            result.findings.append(
                Finding(
                    "V2",
                    Severity.FATAL,
                    f"test_id {value!r} is already used on row {first_seen[value]}. "
                    "Identifiers must be unique; clear this cell to have a new one "
                    "assigned, or restore the original value.",
                    record.line_number,
                    "test_id",
                )
            )
        else:
            first_seen[value] = record.line_number

    minter = _IdentifierMinter(
        existing=[v for v in raw_ids if v],
        reserved=set(published or {}),
    )

    for record, raw_id in zip(records, raw_ids, strict=True):
        payload, findings = _validate_row(record, known_criteria=known_criteria)
        result.findings.extend(findings)
        if payload is None:
            continue

        # T046 -- a blank identifier is a row the user added, not an error.
        if not raw_id:
            payload["test_id"] = minter.mint()
            payload["origin"] = Origin.USER_ADDED.value
            result.assigned_ids.append(str(payload["test_id"]))
        elif not TEST_ID_PATTERN.match(raw_id):
            result.findings.append(
                Finding(
                    "V2",
                    Severity.FATAL,
                    f"test_id {raw_id!r} is not a valid identifier (expected TC-7F3A-001). "
                    "Clear the cell to have a new identifier assigned, or restore the "
                    "original value -- this column must not be edited.",
                    record.line_number,
                    "test_id",
                )
            )
            continue
        else:
            payload["origin"] = Origin.GENERATED.value

        reported_columns: set[str | None] = {f.column for f in findings if f.is_fatal}
        case, model_findings = _build_case(payload, record.line_number, reported_columns)
        result.findings.extend(model_findings)
        if case is not None and record.line_number not in duplicated:
            result.cases.append(case)

    # V9 -- a published case that is no longer in the file. A warning: deleting an
    # already-published row is a reasonable thing to do. It must be visible, not forbidden.
    if published:
        present = {c.test_id for c in result.cases} | {v for v in raw_ids if v}
        for test_id, entry in published.items():
            if entry.is_published and test_id not in present:
                result.findings.append(
                    Finding(
                        "V9",
                        Severity.WARNING,
                        f"{test_id} was published as {entry.issue_key} but is no longer in "
                        "the draft. The Jira issue still exists and is untouched.",
                        None,
                        "test_id",
                    )
                )

    return result
