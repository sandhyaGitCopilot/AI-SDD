"""T038: the tolerant CSV reader (research R6).

CSV was chosen so reviewers can work in a spreadsheet. These tests encode the consequence:
the reader must accept what spreadsheets and humans actually produce -- re-encoded files,
semicolon delimiters, inconsistent renumbering, stray blank lines -- because rejecting any of
those would make the format hostile to the workflow it was chosen for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jira_testgen.draft.csv_io import (
    COLUMNS,
    decode_steps,
    encode_steps,
    read_draft_rows,
    row_to_payload,
)
from jira_testgen.errors import DraftValidationFailure
from jira_testgen.models import Approval, CaseKind, TestCase

pytestmark = pytest.mark.unit

HEADER = ",".join(COLUMNS)


def write(path: Path, body: str, encoding: str = "utf-8-sig", newline: str = "\r\n") -> Path:
    path.write_text(body.replace("\n", newline), encoding=encoding, newline="")
    return path


class TestStepEncoding:
    def test_round_trip(self) -> None:
        steps = ["Open the page", "Enter an email", "Submit"]
        assert decode_steps(encode_steps(steps)) == steps

    def test_numbers_are_added_on_write(self) -> None:
        assert encode_steps(["a", "b"]) == "1. a\n2. b"

    @pytest.mark.parametrize(
        "cell",
        [
            "1. First\n2. Second",
            "1) First\n2) Second",
            "- First\n- Second",
            "* First\n* Second",
            "First\nSecond",
            "1. First\r\n2. Second",
            "1. First\r2. Second",
        ],
    )
    def test_accepts_every_plausible_marker_style(self, cell: str) -> None:
        assert decode_steps(cell) == ["First", "Second"]

    def test_inconsistent_renumbering_preserves_visual_order(self) -> None:
        """Order is positional: the numbers are a reading aid, not the source of truth."""
        assert decode_steps("1. Alpha\n1. Beta\n5. Gamma") == ["Alpha", "Beta", "Gamma"]

    def test_blank_lines_are_dropped(self) -> None:
        assert decode_steps("1. Alpha\n\n\n2. Beta\n") == ["Alpha", "Beta"]

    def test_empty_cell_yields_no_steps(self) -> None:
        assert decode_steps("") == []
        assert decode_steps("   \n  ") == []

    def test_text_containing_a_number_is_not_mangled(self) -> None:
        """Only a *leading* marker is stripped."""
        assert decode_steps("1. Wait 30 seconds, then retry 2. times") == [
            "Wait 30 seconds, then retry 2. times"
        ]

    def test_preserves_internal_punctuation(self) -> None:
        cell = '1. Enter "quoted, value"\n2. Check the 1st result'
        assert decode_steps(cell) == ['Enter "quoted, value"', "Check the 1st result"]


class TestEncodingTolerance:
    BODY = f"# jira-testgen draft v1\n{HEADER}\nTC-7F3A-001,pending,positive,Summary,,1. step,1. result,AC-1,,\n"

    def test_reads_utf8_with_bom(self, tmp_path: Path) -> None:
        path = write(tmp_path / "d.csv", self.BODY, encoding="utf-8-sig")
        assert read_draft_rows(path)[0]["test_id"] == "TC-7F3A-001"

    def test_reads_utf8_without_bom(self, tmp_path: Path) -> None:
        """A text editor may strip the BOM; that must not break the file."""
        path = write(tmp_path / "d.csv", self.BODY, encoding="utf-8")
        assert read_draft_rows(path)[0]["test_id"] == "TC-7F3A-001"

    def test_reads_cp1252_saved_by_a_windows_spreadsheet(self, tmp_path: Path) -> None:
        """Failing with UnicodeDecodeError would be correct and useless to the user."""
        body = self.BODY.replace("Summary", "Café login")
        path = write(tmp_path / "d.csv", body, encoding="cp1252")
        assert "Caf" in read_draft_rows(path)[0]["summary"]

    def test_reads_lf_only_line_endings(self, tmp_path: Path) -> None:
        path = write(tmp_path / "d.csv", self.BODY, newline="\n")
        assert len(read_draft_rows(path)) == 1


class TestDelimiterSniffing:
    def test_reads_semicolon_delimited_file(self, tmp_path: Path) -> None:
        """Excel writes semicolons under some locales (research R6)."""
        header = ";".join(COLUMNS)
        body = f"# preamble\n{header}\nTC-7F3A-001;pending;positive;Summary;;1. step;1. result;AC-1;;\n"
        path = write(tmp_path / "d.csv", body)
        rows = read_draft_rows(path)
        assert rows[0]["test_id"] == "TC-7F3A-001"
        assert rows[0]["summary"] == "Summary"

    def test_comma_wins_when_both_appear(self, tmp_path: Path) -> None:
        body = (
            f"{HEADER}\n"
            'TC-7F3A-001,pending,positive,"A; semicolon; summary",,1. step,1. result,AC-1,,\n'
        )
        path = write(tmp_path / "d.csv", body)
        assert read_draft_rows(path)[0]["summary"] == "A; semicolon; summary"


class TestPreambleHandling:
    def test_preamble_lines_are_skipped(self, tmp_path: Path) -> None:
        body = (
            "# jira-testgen draft v1\n"
            "# run_id: PROJ-123-20261006-120000\n"
            "# DO NOT EDIT the test_id column.\n"
            f"{HEADER}\n"
            "TC-7F3A-001,pending,positive,Summary,,1. step,1. result,AC-1,,\n"
        )
        path = write(tmp_path / "d.csv", body)
        rows = read_draft_rows(path)
        assert len(rows) == 1

    def test_file_without_a_preamble_still_reads(self, tmp_path: Path) -> None:
        body = f"{HEADER}\nTC-7F3A-001,pending,positive,Summary,,1. step,1. result,AC-1,,\n"
        path = write(tmp_path / "d.csv", body)
        assert len(read_draft_rows(path)) == 1

    def test_blank_rows_are_ignored(self, tmp_path: Path) -> None:
        """A trailing blank row is what a spreadsheet leaves behind, not a user error."""
        body = (
            f"{HEADER}\n"
            "TC-7F3A-001,pending,positive,Summary,,1. step,1. result,AC-1,,\n"
            ",,,,,,,,,\n"
            "\n"
        )
        path = write(tmp_path / "d.csv", body)
        assert len(read_draft_rows(path)) == 1


class TestReaderFailures:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(DraftValidationFailure) as exc:
            read_draft_rows(tmp_path / "nope.csv")
        assert exc.value.exit_code == 10

    def test_empty_file(self, tmp_path: Path) -> None:
        path = write(tmp_path / "d.csv", "")
        with pytest.raises(DraftValidationFailure, match="empty"):
            read_draft_rows(path)

    def test_preamble_but_no_table(self, tmp_path: Path) -> None:
        path = write(tmp_path / "d.csv", "# jira-testgen draft v1\n# run_id: x\n")
        with pytest.raises(DraftValidationFailure, match="no table"):
            read_draft_rows(path)

    @pytest.mark.parametrize("dropped", ["test_id", "summary", "steps", "expected_results"])
    def test_missing_required_column_names_it(self, tmp_path: Path, dropped: str) -> None:
        """FR-016: the user must be told which column, not just that something is wrong."""
        remaining = [c for c in COLUMNS if c != dropped]
        body = f"{','.join(remaining)}\n{','.join('x' for _ in remaining)}\n"
        path = write(tmp_path / "d.csv", body)

        with pytest.raises(DraftValidationFailure) as exc:
            read_draft_rows(path)

        assert dropped in exc.value.message
        assert "case-sensitive" in exc.value.remediation

    def test_renamed_column_is_treated_as_missing(self, tmp_path: Path) -> None:
        body = f"{HEADER.replace('summary', 'Summary')}\n"
        path = write(tmp_path / "d.csv", body)
        with pytest.raises(DraftValidationFailure, match="summary"):
            read_draft_rows(path)


class TestRowToPayload:
    def _row(self, **overrides: str) -> dict[str, str]:
        row = {
            "test_id": "TC-7F3A-001",
            "approval": "approved",
            "kind": "negative",
            "summary": "Rejects an unregistered email",
            "preconditions": "An account exists",
            "steps": "1. Open page\n2. Submit",
            "expected_results": "1. Loads\n2. Error shows",
            "traces_to": "AC-1, AC-2",
            "jira_key": "",
            "notes": "",
        }
        row.update(overrides)
        return row

    def test_builds_a_valid_test_case(self) -> None:
        case = TestCase(**row_to_payload(self._row()))
        assert case.test_id == "TC-7F3A-001"
        assert case.approval is Approval.APPROVED
        assert case.case_kind is CaseKind.NEGATIVE
        assert case.steps == ["Open page", "Submit"]
        assert case.traces_to == ["AC-1", "AC-2"]

    def test_approval_and_kind_are_case_insensitive(self) -> None:
        case = TestCase(**row_to_payload(self._row(approval="APPROVED", kind="Edge")))
        assert case.approval is Approval.APPROVED
        assert case.case_kind is CaseKind.EDGE

    def test_blank_approval_defaults_to_pending(self) -> None:
        case = TestCase(**row_to_payload(self._row(approval="")))
        assert case.approval is Approval.PENDING

    def test_blank_test_id_is_preserved_for_user_added_rows(self) -> None:
        """FR-023a: a hand-typed row gets its identifier at validation time, not here."""
        assert row_to_payload(self._row(test_id=""))["test_id"] == ""

    def test_traces_to_is_split_and_trimmed(self) -> None:
        payload = row_to_payload(self._row(traces_to="  AC-1 ,AC-2,  , AC-3 "))
        assert payload["traces_to"] == ["AC-1", "AC-2", "AC-3"]

    def test_whitespace_around_values_is_trimmed(self) -> None:
        payload = row_to_payload(self._row(summary="   padded summary   "))
        assert payload["summary"] == "padded summary"
