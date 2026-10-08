"""T022: the draft CSV contract (contracts/draft-csv.md).

This is the artifact every user touches, so its shape is pinned here: exact headers, exact
encoding, and a round trip that survives commas, quotes, and embedded newlines.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from jira_testgen.draft.csv_io import (
    COLUMNS,
    REQUIRED_COLUMNS,
    read_draft_rows,
    write_draft,
)
from jira_testgen.models import (
    AcceptanceCriterion,
    Approval,
    CaseKind,
    Draft,
    SourceRequirement,
    TestCase,
)

pytestmark = pytest.mark.contract


def make_draft(cases: list[TestCase] | None = None, **source_kw: object) -> Draft:
    source_data: dict[str, object] = {
        "issue_key": "PROJ-123",
        "summary": "Users can reset their password by email",
        "description_text": "Users can reset their password.",
        "project_key": "PROJ",
        "fields_read": ["summary", "description", "Acceptance Criteria"],
        "criteria": [AcceptanceCriterion(criterion_id="AC-1", text="Reset email is sent")],
    }
    source_data.update(source_kw)
    return Draft(
        run_id="PROJ-123-20261006-142233",
        source=SourceRequirement(**source_data),  # type: ignore[arg-type]
        test_cases=cases
        or [
            TestCase(
                test_id="TC-7F3A-001",
                summary="Reset email is sent",
                preconditions="An account exists.",
                steps=["Open the reset page", "Submit a registered email"],
                expected_results=["The page loads", "A confirmation is shown"],
                traces_to=["AC-1"],
                case_kind=CaseKind.POSITIVE,
            )
        ],
        coverage_notes=["AC-2 not covered - case limit reached"],
        generation_service="anthropic/claude-opus-5-5",
    )


class TestColumnContract:
    def test_exact_column_names_and_order(self) -> None:
        assert COLUMNS == [
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

    def test_required_subset(self) -> None:
        assert REQUIRED_COLUMNS == [
            "test_id",
            "approval",
            "kind",
            "summary",
            "steps",
            "expected_results",
            "traces_to",
        ]

    def test_header_row_written_exactly(self, tmp_path: Path) -> None:
        path = write_draft(tmp_path / "testcases.csv", make_draft())
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        header = next(r for r in rows if r and not r[0].startswith("#"))
        assert header == COLUMNS


class TestEncoding:
    def test_written_with_utf8_bom(self, tmp_path: Path) -> None:
        """Excel misreads UTF-8 without a BOM -- research R6."""
        path = write_draft(tmp_path / "testcases.csv", make_draft())
        assert path.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_crlf_line_terminators(self, tmp_path: Path) -> None:
        path = write_draft(tmp_path / "testcases.csv", make_draft())
        assert b"\r\n" in path.read_bytes()

    def test_non_ascii_survives(self, tmp_path: Path) -> None:
        case = TestCase(
            test_id="TC-7F3A-001",
            summary="Rejects an invalid e-mail - naive input",
            steps=["Enter 'resume' with accents: resume"],
            expected_results=["An error appears"],
            traces_to=["AC-1"],
        )
        path = write_draft(tmp_path / "testcases.csv", make_draft([case]))
        rows = read_draft_rows(path)
        assert "accents" in rows[0]["steps"]


class TestPreamble:
    def test_preamble_lines_are_comments(self, tmp_path: Path) -> None:
        path = write_draft(tmp_path / "testcases.csv", make_draft())
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        preamble = [ln for ln in lines if ln.startswith("#")]
        assert len(preamble) >= 5
        assert all(ln.startswith("#") for ln in preamble)

    def test_preamble_carries_provenance(self, tmp_path: Path) -> None:
        """FR-002, FR-009, FR-030: the reviewer must see what was read and what produced it."""
        path = write_draft(tmp_path / "testcases.csv", make_draft())
        text = path.read_text(encoding="utf-8-sig")
        assert "run_id: PROJ-123-20261006-142233" in text
        assert "source_issue: PROJ-123" in text
        assert "Acceptance Criteria" in text
        assert "anthropic/claude-opus-5-5" in text
        assert "AC-2 not covered" in text

    def test_preamble_warns_against_editing_identifiers(self, tmp_path: Path) -> None:
        path = write_draft(tmp_path / "testcases.csv", make_draft())
        assert "DO NOT EDIT" in path.read_text(encoding="utf-8-sig")

    def test_preamble_is_skipped_on_read(self, tmp_path: Path) -> None:
        path = write_draft(tmp_path / "testcases.csv", make_draft())
        rows = read_draft_rows(path)
        assert len(rows) == 1
        assert rows[0]["test_id"] == "TC-7F3A-001"


class TestQuotingRoundTrip:
    @pytest.mark.parametrize(
        "nasty",
        [
            "Summary, with a comma",
            'Summary with "quotes"',
            "Summary with 'single' quotes",
            "Summary; with a semicolon",
        ],
    )
    def test_special_characters_survive(self, tmp_path: Path, nasty: str) -> None:
        case = TestCase(
            test_id="TC-7F3A-001",
            summary=nasty,
            steps=["step"],
            expected_results=["result"],
            traces_to=["AC-1"],
        )
        path = write_draft(tmp_path / "testcases.csv", make_draft([case]))
        assert read_draft_rows(path)[0]["summary"] == nasty

    def test_embedded_newlines_in_steps_survive(self, tmp_path: Path) -> None:
        """Multi-line cells are the whole reason CSV needed care here (FR-012b)."""
        case = TestCase(
            test_id="TC-7F3A-001",
            summary="Multi-step case",
            steps=["First step", "Second step, with comma", 'Third "quoted" step'],
            expected_results=["One result overall"],
            traces_to=["AC-1"],
        )
        path = write_draft(tmp_path / "testcases.csv", make_draft([case]))
        steps = read_draft_rows(path)[0]["steps"]
        assert "1. First step" in steps
        assert "2. Second step, with comma" in steps
        assert '3. Third "quoted" step' in steps

    def test_full_draft_round_trip(self, tmp_path: Path) -> None:
        original = make_draft(
            [
                TestCase(
                    test_id="TC-7F3A-001",
                    summary="Positive path",
                    preconditions="Account exists",
                    steps=["a", "b"],
                    expected_results=["x", "y"],
                    traces_to=["AC-1"],
                    case_kind=CaseKind.POSITIVE,
                    approval=Approval.APPROVED,
                ),
                TestCase(
                    test_id="TC-7F3A-002",
                    summary="Negative path",
                    steps=["a"],
                    expected_results=["error"],
                    traces_to=["AC-1", "REQ"],
                    case_kind=CaseKind.NEGATIVE,
                ),
            ]
        )
        path = write_draft(tmp_path / "testcases.csv", original)
        rows = read_draft_rows(path)

        assert [r["test_id"] for r in rows] == ["TC-7F3A-001", "TC-7F3A-002"]
        assert rows[0]["approval"] == "approved"
        assert rows[1]["kind"] == "negative"
        assert rows[1]["traces_to"] == "AC-1, REQ"


class TestIdentifierSafety:
    def test_identifier_is_not_numeric(self, tmp_path: Path) -> None:
        """Research R6: a numeric-looking id gets coerced by spreadsheets."""
        path = write_draft(tmp_path / "testcases.csv", make_draft())
        test_id = read_draft_rows(path)[0]["test_id"]
        assert test_id.startswith("TC-")
        with pytest.raises(ValueError):
            float(test_id)

    def test_jira_key_column_is_empty_before_publishing(self, tmp_path: Path) -> None:
        path = write_draft(tmp_path / "testcases.csv", make_draft())
        assert read_draft_rows(path)[0]["jira_key"] == ""
