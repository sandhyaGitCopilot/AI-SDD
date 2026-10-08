"""T047: each draft validation rule in isolation (contracts/draft-csv.md V1-V11).

The integration test in ``tests/integration/test_draft_validation.py`` proves the rules
reach the user as exit code 10. This file proves each rule *individually* -- which one
fired, on which row, naming which column -- because FR-016 promises the user a finding
they can act on, and "the draft is invalid" is not that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jira_testgen.draft import validate as validate_mod
from jira_testgen.draft.csv_io import write_draft
from jira_testgen.errors import DraftValidationFailure
from jira_testgen.models import Approval, Origin, PublicationEntry

pytestmark = pytest.mark.unit

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

PREAMBLE = ["jira-testgen draft v1", "run_id: PROJ-1-x", "DO NOT EDIT the test_id column."]


def row(index: int = 1, **overrides: str) -> dict[str, str]:
    base = {
        "test_id": f"TC-AB12-{index:03d}",
        "approval": "approved",
        "kind": "positive",
        "summary": f"Verify behaviour {index}",
        "preconditions": "",
        "steps": "1. Open the page\n2. Submit",
        "expected_results": "1. Page loads\n2. Notice appears",
        "traces_to": "AC-1",
        "jira_key": "",
        "notes": "",
    }
    base.update(overrides)
    return base


def write_csv(
    path: Path,
    rows: list[dict[str, str]],
    *,
    columns: list[str] | None = None,
    preamble: list[str] | None = None,
    delimiter: str = ",",
    encoding: str = "utf-8-sig",
) -> Path:
    import csv
    import io

    buffer = io.StringIO(newline="")
    for line in PREAMBLE if preamble is None else preamble:
        buffer.write(f"# {line}\r\n")
    writer = csv.DictWriter(
        buffer,
        fieldnames=columns or COLUMNS,
        delimiter=delimiter,
        lineterminator="\r\n",
        extrasaction="ignore",
    )
    writer.writeheader()
    for item in rows:
        writer.writerow(item)
    path.write_bytes(buffer.getvalue().encode(encoding))
    return path


def check(
    tmp_path: Path, rows: list[dict[str, str]], **kwargs: object
) -> validate_mod.ValidationResult:
    path = write_csv(tmp_path / "testcases.csv", rows, **kwargs)  # type: ignore[arg-type]
    return validate_mod.validate_draft(path, known_criteria={"AC-1", "AC-2", "AC-3"})


def rules(result: validate_mod.ValidationResult) -> set[str]:
    return {f.rule for f in result.findings}


def fatal_rules(result: validate_mod.ValidationResult) -> set[str]:
    return {f.rule for f in result.findings if f.is_fatal}


# ---------------------------------------------------------------------------
# A clean draft
# ---------------------------------------------------------------------------


class TestValidDraft:
    def test_clean_draft_has_no_findings(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1), row(2)])
        assert result.findings == []
        assert result.is_valid
        assert len(result.cases) == 2

    def test_cases_are_parsed_into_models(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, approval="rejected")])
        assert result.cases[0].approval is Approval.REJECTED
        assert result.cases[0].steps == ["Open the page", "Submit"]

    def test_generated_rows_keep_generated_origin(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1)])
        assert result.cases[0].origin is Origin.GENERATED


# ---------------------------------------------------------------------------
# V1 - required columns
# ---------------------------------------------------------------------------


class TestV1RequiredColumns:
    def test_missing_column_is_fatal_and_names_it(self, tmp_path: Path) -> None:
        columns = [c for c in COLUMNS if c != "expected_results"]
        with pytest.raises(DraftValidationFailure) as exc:
            check(tmp_path, [row(1)], columns=columns)
        assert "expected_results" in str(exc.value)

    def test_optional_columns_may_be_absent(self, tmp_path: Path) -> None:
        columns = [c for c in COLUMNS if c not in {"notes", "jira_key", "preconditions"}]
        result = check(tmp_path, [row(1)], columns=columns)
        assert result.is_valid


# ---------------------------------------------------------------------------
# V2 - unique identifiers (FR-016: report BOTH rows)
# ---------------------------------------------------------------------------


class TestV2UniqueIdentifiers:
    def test_duplicate_is_fatal(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1), row(2, test_id="TC-AB12-001")])
        assert "V2" in fatal_rules(result)

    def test_duplicate_reports_both_row_numbers(self, tmp_path: Path) -> None:
        """A finding naming only the second row sends the user hunting for the first."""
        result = check(tmp_path, [row(1), row(2, test_id="TC-AB12-001")])
        finding = next(f for f in result.findings if f.rule == "V2")
        # Preamble (3) + header (1) = 4, so row 1 starts at line 5. Its two-line `steps`
        # and `expected_results` cells make it span lines 5-7, putting row 2 on line 8.
        assert finding.row == 8
        assert "5" in finding.message
        assert finding.column == "test_id"

    def test_row_numbers_account_for_multi_line_cells(self, tmp_path: Path) -> None:
        """An editor numbers the physical lines, so embedded newlines must shift the count."""
        tall = row(1, steps="1. A\n2. B\n3. C\n4. D", expected_results="1. All pass")
        result = check(tmp_path, [tall, row(2, test_id="TC-AB12-001")])
        finding = next(f for f in result.findings if f.rule == "V2")
        assert finding.row == 9  # 4 header lines + a 4-line row at 5-8, so the duplicate is 9


# ---------------------------------------------------------------------------
# V3 / V4 - enumerated values
# ---------------------------------------------------------------------------


class TestV3Approval:
    def test_unknown_value_is_fatal(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, approval="maybe")])
        assert "V3" in fatal_rules(result)
        assert next(f for f in result.findings if f.rule == "V3").column == "approval"

    @pytest.mark.parametrize("value", ["APPROVED", "Approved", " approved "])
    def test_case_and_whitespace_are_tolerated(self, tmp_path: Path, value: str) -> None:
        result = check(tmp_path, [row(1, approval=value)])
        assert result.is_valid
        assert result.cases[0].approval is Approval.APPROVED

    def test_blank_approval_defaults_to_pending(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, approval="")])
        assert result.is_valid
        assert result.cases[0].approval is Approval.PENDING


class TestV4Kind:
    def test_unknown_kind_is_fatal(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, kind="smoke")])
        assert "V4" in fatal_rules(result)
        assert next(f for f in result.findings if f.rule == "V4").column == "kind"

    @pytest.mark.parametrize("value", ["positive", "NEGATIVE", "Edge"])
    def test_allowed_kinds(self, tmp_path: Path, value: str) -> None:
        assert check(tmp_path, [row(1, kind=value)]).is_valid


# ---------------------------------------------------------------------------
# V5 / V6 / V7 - case content
# ---------------------------------------------------------------------------


class TestV5Summary:
    def test_empty_summary_is_fatal(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, summary="   ")])
        assert "V5" in fatal_rules(result)

    def test_over_255_characters_is_fatal(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, summary="x" * 256)])
        assert "V5" in fatal_rules(result)

    def test_exactly_255_is_allowed(self, tmp_path: Path) -> None:
        assert check(tmp_path, [row(1, summary="x" * 255)]).is_valid


class TestV6Steps:
    def test_no_steps_is_fatal(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, steps="", expected_results="1. Something")])
        assert "V6" in fatal_rules(result)

    def test_only_numbering_is_not_a_step(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, steps="1.\n2.", expected_results="1. Something")])
        assert "V6" in fatal_rules(result)


class TestV7ExpectedResults:
    def test_mismatched_count_is_fatal(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, steps="1. A\n2. B\n3. C", expected_results="1. X\n2. Y")])
        assert "V7" in fatal_rules(result)

    def test_one_overall_result_is_allowed(self, tmp_path: Path) -> None:
        assert check(
            tmp_path, [row(1, steps="1. A\n2. B\n3. C", expected_results="Everything passes")]
        ).is_valid

    def test_one_per_step_is_allowed(self, tmp_path: Path) -> None:
        assert check(tmp_path, [row(1, steps="1. A\n2. B", expected_results="1. X\n2. Y")]).is_valid

    def test_empty_expected_results_is_fatal(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, expected_results="")])
        assert fatal_rules(result) & {"V7"}


# ---------------------------------------------------------------------------
# V8 - unknown criterion id (warning, not fatal)
# ---------------------------------------------------------------------------


class TestV8TracesTo:
    def test_unknown_criterion_is_a_warning_only(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, traces_to="AC-9")])
        assert "V8" in rules(result)
        assert fatal_rules(result) == set()
        assert result.is_valid

    def test_req_is_always_accepted(self, tmp_path: Path) -> None:
        assert check(tmp_path, [row(1, traces_to="REQ")]).findings == []

    def test_empty_traces_to_is_fatal(self, tmp_path: Path) -> None:
        """A case tracing to nothing cannot be reviewed for coverage."""
        result = check(tmp_path, [row(1, traces_to="")])
        assert fatal_rules(result)


# ---------------------------------------------------------------------------
# V9 - a published case deleted from the file (warning, FR-016)
# ---------------------------------------------------------------------------


class TestV9MissingPublishedRow:
    def test_missing_published_id_is_a_warning(self, tmp_path: Path) -> None:
        path = write_csv(tmp_path / "testcases.csv", [row(1)])
        record = {
            "TC-AB12-002": PublicationEntry(test_id="TC-AB12-002", issue_key="PROJ-900"),
        }
        result = validate_mod.validate_draft(path, published=record, known_criteria={"AC-1"})
        assert "V9" in rules(result)
        assert fatal_rules(result) == set()

    def test_present_published_id_is_silent(self, tmp_path: Path) -> None:
        path = write_csv(tmp_path / "testcases.csv", [row(1)])
        record = {"TC-AB12-001": PublicationEntry(test_id="TC-AB12-001", issue_key="PROJ-900")}
        result = validate_mod.validate_draft(path, published=record, known_criteria={"AC-1"})
        assert "V9" not in rules(result)


# ---------------------------------------------------------------------------
# V10 - the 25-case cap
# ---------------------------------------------------------------------------


class TestV10RowCap:
    def test_exactly_25_is_allowed(self, tmp_path: Path) -> None:
        assert check(tmp_path, [row(i) for i in range(1, 26)]).is_valid

    def test_26_is_fatal(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(i) for i in range(1, 27)])
        assert "V10" in fatal_rules(result)

    def test_zero_rows_is_fatal(self, tmp_path: Path) -> None:
        with pytest.raises(DraftValidationFailure):
            check(tmp_path, [])


# ---------------------------------------------------------------------------
# V11 - readable as CSV at all
# ---------------------------------------------------------------------------


class TestV11Unreadable:
    def test_missing_file_is_fatal(self, tmp_path: Path) -> None:
        with pytest.raises(DraftValidationFailure):
            validate_mod.validate_draft(tmp_path / "absent.csv")

    def test_empty_file_is_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "testcases.csv"
        path.write_bytes(b"")
        with pytest.raises(DraftValidationFailure):
            validate_mod.validate_draft(path)

    def test_preamble_without_a_table_is_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "testcases.csv"
        path.write_bytes(b"# jira-testgen draft v1\r\n# run_id: x\r\n")
        with pytest.raises(DraftValidationFailure):
            validate_mod.validate_draft(path)


# ---------------------------------------------------------------------------
# T046 - identifier handling for edited drafts
# ---------------------------------------------------------------------------


class TestUserAddedRows:
    def test_blank_identifier_is_accepted(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1), row(2, test_id="")])
        assert result.is_valid

    def test_blank_identifier_is_marked_user_added(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1), row(2, test_id="")])
        added = result.cases[1]
        assert added.origin is Origin.USER_ADDED
        assert result.cases[0].origin is Origin.GENERATED

    def test_blank_identifier_gets_a_fresh_well_formed_id(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1), row(2, test_id="")])
        assert result.cases[1].test_id == "TC-AB12-002"
        assert result.cases[1].test_id in result.assigned_ids

    def test_fresh_ids_reuse_the_run_sequence_of_the_draft(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, test_id="TC-9F3A-001"), row(2, test_id="")])
        assert result.cases[1].test_id.startswith("TC-9F3A-")

    def test_several_blank_rows_each_get_a_distinct_id(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1), row(2, test_id=""), row(3, test_id="")])
        ids = [c.test_id for c in result.cases]
        assert len(set(ids)) == 3

    def test_a_wholly_blank_draft_still_gets_valid_ids(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, test_id=""), row(2, test_id="")])
        assert all(c.origin is Origin.USER_ADDED for c in result.cases)
        assert len({c.test_id for c in result.cases}) == 2

    def test_a_malformed_identifier_is_fatal(self, tmp_path: Path) -> None:
        """Blank means 'new'. Garbage means the column was edited, which FR-006a forbids."""
        result = check(tmp_path, [row(1, test_id="42")])
        assert fatal_rules(result)

    def test_a_changed_identifier_is_simply_treated_as_new(self, tmp_path: Path) -> None:
        path = write_csv(tmp_path / "testcases.csv", [row(1, test_id="TC-AB12-099")])
        record = {"TC-AB12-001": PublicationEntry(test_id="TC-AB12-001", issue_key="PROJ-900")}
        result = validate_mod.validate_draft(path, published=record, known_criteria={"AC-1"})
        assert result.cases[0].test_id == "TC-AB12-099"
        assert "V9" in rules(result)  # the original is now missing -- visible, not forbidden


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class TestFindingReporting:
    def test_findings_render_with_row_and_column(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, kind="smoke")])
        rendered = result.findings[0].render()
        assert "row 5" in rendered
        assert "kind" in rendered

    def test_raise_if_fatal_exits_10_and_carries_findings(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, kind="smoke")])
        with pytest.raises(DraftValidationFailure) as exc:
            result.raise_if_fatal()
        assert exc.value.exit_code == 10
        assert exc.value.findings

    def test_raise_if_fatal_is_silent_on_warnings(self, tmp_path: Path) -> None:
        check(tmp_path, [row(1, traces_to="AC-9")]).raise_if_fatal()

    def test_every_finding_names_a_row(self, tmp_path: Path) -> None:
        result = check(tmp_path, [row(1, kind="smoke"), row(2, approval="maybe")])
        assert all(f.row is not None for f in result.findings)

    def test_all_rows_are_reported_not_just_the_first(self, tmp_path: Path) -> None:
        """A validator that stops at the first bad row makes fixing a draft an N-pass job."""
        result = check(tmp_path, [row(1, kind="smoke"), row(2, kind="smoke")])
        assert len([f for f in result.findings if f.rule == "V4"]) == 2


# ---------------------------------------------------------------------------
# Round trip through the real writer
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_a_draft_this_tool_wrote_validates(self, tmp_path: Path) -> None:
        from jira_testgen.models import Draft, SourceRequirement, TestCase

        source = SourceRequirement(issue_key="PROJ-123", description_text="Something testable.")
        draft = Draft(
            run_id="PROJ-123-x",
            source=source,
            test_cases=[
                TestCase(
                    test_id="TC-AB12-001",
                    summary='A case with a comma, a "quote", and a tab\tcharacter',
                    steps=["One", "Two"],
                    expected_results=["Done"],
                    traces_to=["REQ"],
                )
            ],
        )
        path = write_draft(tmp_path / "testcases.csv", draft)
        result = validate_mod.validate_draft(path)
        assert result.is_valid
        assert result.cases[0].summary == draft.test_cases[0].summary
