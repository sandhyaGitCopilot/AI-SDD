"""Domain models, mirroring data-model.md.

Constraints live here rather than at the call sites, so model output (contracts/generation.md)
and hand-edited CSV rows (contracts/draft-csv.md) go through exactly one validation path. Two
sources of input, one definition of valid.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]+-[0-9]+$")
TEST_ID_PATTERN = re.compile(r"^TC-[A-Z0-9]{4}-[0-9]{3}$")

MAX_SUMMARY_LENGTH = 255
MAX_TEST_CASES = 25


def utcnow() -> datetime:
    return datetime.now(UTC)


class CaseKind(StrEnum):
    """Makes the 30% negative/edge floor in SC-005 measurable instead of a matter of opinion."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    EDGE = "edge"


class Approval(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Origin(StrEnum):
    GENERATED = "generated"
    USER_ADDED = "user_added"


class RunPhase(StrEnum):
    DRAFTED = "drafted"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED = "failed"


class LinkedIssueRef(BaseModel):
    """An issue already linked to the requirement, for the FR-005 pre-generation report."""

    model_config = ConfigDict(frozen=True)

    issue_key: str
    summary: str = ""
    link_type: str = ""
    issue_type: str = ""


class AcceptanceCriterion(BaseModel):
    model_config = ConfigDict(frozen=True)

    criterion_id: str = Field(pattern=r"^AC-[0-9]+$")
    text: str = Field(min_length=1)
    source_field: str = ""
    #: False when criteria were prose rather than a list; drives the SC-004 shortfall note.
    machine_readable: bool = True


class SourceRequirement(BaseModel):
    """A frozen snapshot of the Jira issue. Later steps read this, never Jira again."""

    issue_key: str
    summary: str = ""
    description_text: str = ""
    acceptance_criteria_text: str | None = None
    criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    fields_read: list[str] = Field(default_factory=list)
    unread_node_types: list[str] = Field(default_factory=list)
    existing_linked_tests: list[LinkedIssueRef] = Field(default_factory=list)
    project_key: str = ""
    read_at: datetime = Field(default_factory=utcnow)

    @field_validator("issue_key")
    @classmethod
    def _validate_issue_key(cls, value: str) -> str:
        if not ISSUE_KEY_PATTERN.match(value):
            raise ValueError(f"{value!r} is not a Jira issue key. Expected a form like PROJ-123.")
        return value

    @model_validator(mode="after")
    def _require_usable_content(self) -> SourceRequirement:
        """FR-004: refuse a requirement with nothing to generate from.

        Checked here so the stop happens before a model call is made and before any draft is
        written, rather than after money has been spent.
        """
        description = (self.description_text or "").strip()
        criteria = (self.acceptance_criteria_text or "").strip()
        if not description and not criteria:
            raise ValueError(f"{self.issue_key} has no description and no acceptance criteria.")
        return self

    def has_machine_readable_criteria(self) -> bool:
        return any(c.machine_readable for c in self.criteria)


class TestCase(BaseModel):
    """One manual test. ``test_id`` is the load-bearing field -- see research R4."""

    # Tells pytest not to collect this as a test class; the name is domain vocabulary here.
    __test__ = False

    model_config = ConfigDict(validate_assignment=True)

    test_id: str = ""
    summary: Annotated[str, Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)]
    preconditions: str = ""
    steps: list[str] = Field(min_length=1)
    expected_results: list[str] = Field(min_length=1)
    traces_to: list[str] = Field(min_length=1)
    case_kind: CaseKind = CaseKind.POSITIVE
    approval: Approval = Approval.PENDING
    origin: Origin = Origin.GENERATED
    notes: str = ""

    @field_validator("test_id")
    @classmethod
    def _validate_test_id(cls, value: str) -> str:
        # Blank is legitimate: a row the user typed by hand gets an identifier at validation
        # time (FR-023a). A *non-blank* identifier must be well formed.
        if value and not TEST_ID_PATTERN.match(value):
            raise ValueError(f"{value!r} is not a valid test id. Expected the form TC-7F3A-001.")
        return value

    @field_validator("steps", "expected_results")
    @classmethod
    def _no_blank_entries(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if not cleaned:
            raise ValueError("must contain at least one non-empty entry")
        return cleaned

    @model_validator(mode="after")
    def _expected_results_count(self) -> TestCase:
        """Either one expected result per step, or exactly one for the case overall.

        Anything else is unreviewable: a tester cannot tell which step a result belongs to.
        """
        n_steps = len(self.steps)
        n_results = len(self.expected_results)
        if n_results != 1 and n_results != n_steps:
            raise ValueError(
                f"expected_results must hold exactly 1 entry or exactly {n_steps} "
                f"(one per step); got {n_results}"
            )
        return self

    @property
    def is_approved(self) -> bool:
        return self.approval is Approval.APPROVED

    @property
    def is_negative_or_edge(self) -> bool:
        return self.case_kind in (CaseKind.NEGATIVE, CaseKind.EDGE)


class Draft(BaseModel):
    """A run's test cases plus the provenance a reviewer needs to judge them."""

    run_id: str
    source: SourceRequirement
    test_cases: list[TestCase] = Field(min_length=1, max_length=MAX_TEST_CASES)
    coverage_notes: list[str] = Field(default_factory=list)
    generation_service: str = ""
    generated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _unique_test_ids(self) -> Draft:
        seen: set[str] = set()
        for case in self.test_cases:
            if not case.test_id:
                continue
            if case.test_id in seen:
                raise ValueError(f"duplicate test id {case.test_id!r} in draft")
            seen.add(case.test_id)
        return self

    @property
    def approved_cases(self) -> list[TestCase]:
        return [c for c in self.test_cases if c.is_approved]

    @property
    def negative_edge_ratio(self) -> float:
        if not self.test_cases:
            return 0.0
        return sum(c.is_negative_or_edge for c in self.test_cases) / len(self.test_cases)


class PublicationEntry(BaseModel):
    """One test case's publication outcome.

    ``issue_key`` and ``linked`` are tracked separately on purpose: creating the issue and
    linking it are two API calls, and a crash between them leaves a real issue with no link.
    Recording them apart is what makes that state repairable rather than permanent.
    """

    test_id: str
    issue_key: str | None = None
    issue_url: str | None = None
    linked: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    last_error: str | None = None

    @property
    def is_published(self) -> bool:
        return self.issue_key is not None

    @property
    def needs_link_repair(self) -> bool:
        return self.issue_key is not None and not self.linked


class RunState(BaseModel):
    """Authoritative, tool-owned run record. Never derived from the user-editable CSV."""

    schema_version: int = 1
    run_id: str
    phase: RunPhase = RunPhase.DRAFTED
    source_snapshot: SourceRequirement
    draft_path: str
    target_project_key: str
    issue_type_id: str = ""
    issue_type_name: str = ""
    link_type_name: str = ""
    publication_record: dict[str, PublicationEntry] = Field(default_factory=dict)
    generation_meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def published_ids(self) -> set[str]:
        return {tid for tid, e in self.publication_record.items() if e.is_published}

    def entries_needing_link(self) -> list[PublicationEntry]:
        return [e for e in self.publication_record.values() if e.needs_link_repair]

    def outstanding(self, approved_ids: list[str]) -> list[str]:
        """Approved cases with no recorded issue yet -- the FR-023 skip logic."""
        published = self.published_ids()
        return [tid for tid in approved_ids if tid not in published]
