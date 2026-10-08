"""T043: drafts saved by a spreadsheet application still validate (SC-012).

Every fixture read here lives in ``tests/fixtures/`` and carries the *same three test
cases* written the way a different application saves them -- BOM or no BOM, comma or
semicolon, ``\\r\\n`` or ``\\n``, minimal or total quoting, steps renumbered by hand.

The reason the fixtures are checked in rather than written by the test is stated in
``tests/fixtures/README.md``: a round-trip test that reads back the tool's own output
would pass while the actual failure mode -- re-encoded, re-delimited, re-quoted content --
stayed untested. Read that file before trusting these: it also records how faithfully the
fixtures reproduce each application.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import typer

from jira_testgen.commands.approve import run_approve
from jira_testgen.draft.validate import validate_draft

pytestmark = pytest.mark.integration

FIXTURES = [
    "spreadsheet-excel-windows.csv",
    "spreadsheet-libreoffice-semicolon.csv",
    "spreadsheet-googlesheets.csv",
    "spreadsheet-hand-renumbered.csv",
]

#: The content every fixture must yield, whatever the encoding did to it.
EXPECTED_IDS = ["TC-AB12-001", "TC-AB12-002", "TC-AB12-003"]

EXPECTED_STEPS = {
    "TC-AB12-001": [
        "Open the password reset page",
        "Enter qa@example.com",
        "Submit the form",
    ],
    "TC-AB12-002": [
        "Open the password reset page",
        "Enter nobody@example.com",
        "Submit the form",
    ],
    "TC-AB12-003": [
        "Open the expired reset link",
        "Attempt to set a new password",
    ],
}

KNOWN_CRITERIA = {"AC-1", "AC-2", "AC-3"}


@pytest.fixture(params=FIXTURES)
def saved_draft(request: Any, fixtures_dir: Path) -> Path:
    path = fixtures_dir / request.param
    assert path.exists(), f"missing fixture {request.param}; see tests/fixtures/README.md"
    return path


class TestEveryFixtureValidates:
    def test_it_parses_at_all(self, saved_draft: Path) -> None:
        result = validate_draft(saved_draft, known_criteria=KNOWN_CRITERIA)
        assert result.is_valid, [f.render() for f in result.findings]

    def test_identifiers_survive(self, saved_draft: Path) -> None:
        """SC-012: a re-encoded identifier that no longer matches is a silent duplicate
        later, because the publication record is keyed on it (research R4)."""
        result = validate_draft(saved_draft, known_criteria=KNOWN_CRITERIA)
        assert [c.test_id for c in result.cases] == EXPECTED_IDS

    def test_no_identifier_was_coerced_to_a_number_or_date(self, saved_draft: Path) -> None:
        result = validate_draft(saved_draft, known_criteria=KNOWN_CRITERIA)
        for case in result.cases:
            assert case.test_id.startswith("TC-")
            assert not case.test_id.replace("-", "").isdigit()

    def test_step_order_survives(self, saved_draft: Path) -> None:
        result = validate_draft(saved_draft, known_criteria=KNOWN_CRITERIA)
        for case in result.cases:
            assert case.steps == EXPECTED_STEPS[case.test_id]

    def test_step_numbering_is_stripped_however_it_was_written(self, saved_draft: Path) -> None:
        """Including the hand-renumbered fixture, where the reviewer used 1) - 3. by hand."""
        result = validate_draft(saved_draft, known_criteria=KNOWN_CRITERIA)
        for case in result.cases:
            for step in case.steps:
                assert not step[0].isdigit()
                assert not step.startswith("-")

    def test_expected_results_counts_survive(self, saved_draft: Path) -> None:
        result = validate_draft(saved_draft, known_criteria=KNOWN_CRITERIA)
        by_id = {c.test_id: c for c in result.cases}
        assert len(by_id["TC-AB12-001"].expected_results) == 3
        assert len(by_id["TC-AB12-002"].expected_results) == 1  # one overall
        assert len(by_id["TC-AB12-003"].expected_results) == 2

    def test_a_cell_containing_a_quote_survives(self, saved_draft: Path) -> None:
        result = validate_draft(saved_draft, known_criteria=KNOWN_CRITERIA)
        by_id = {c.test_id: c for c in result.cases}
        assert '"no such user"' in by_id["TC-AB12-002"].summary

    def test_the_approval_column_survives(self, saved_draft: Path) -> None:
        result = validate_draft(saved_draft, known_criteria=KNOWN_CRITERIA)
        by_id = {c.test_id: c for c in result.cases}
        assert by_id["TC-AB12-001"].is_approved
        assert by_id["TC-AB12-002"].is_approved
        assert not by_id["TC-AB12-003"].is_approved  # rejected

    def test_traces_to_survives(self, saved_draft: Path) -> None:
        result = validate_draft(saved_draft, known_criteria=KNOWN_CRITERIA)
        assert [c.traces_to for c in result.cases] == [["AC-1"], ["AC-2"], ["AC-3"]]


class TestEncodingSpecifics:
    def test_the_bom_variants_disagree_on_disk(self, fixtures_dir: Path) -> None:
        """Guards the fixtures themselves: if every file became identical, this suite would
        be asserting one encoding four times and claiming to cover four."""
        with_bom = (fixtures_dir / "spreadsheet-excel-windows.csv").read_bytes()
        without_bom = (fixtures_dir / "spreadsheet-googlesheets.csv").read_bytes()
        assert with_bom.startswith(b"\xef\xbb\xbf")
        assert not without_bom.startswith(b"\xef\xbb\xbf")

    def test_the_semicolon_fixture_really_uses_semicolons(self, fixtures_dir: Path) -> None:
        body = (fixtures_dir / "spreadsheet-libreoffice-semicolon.csv").read_bytes()
        header = next(ln for ln in body.split(b"\r\n") if ln.startswith(b'"test_id"'))
        assert header.count(b";") == 9

    def test_excel_writes_crlf_inside_quoted_cells(self, fixtures_dir: Path) -> None:
        """The detail most hand-written fixtures get wrong, and the one most likely to
        break a naive line-splitting reader."""
        body = (fixtures_dir / "spreadsheet-excel-windows.csv").read_bytes()
        assert b"Open the password reset page\r\n2. Enter qa@example.com" in body

    def test_google_sheets_fixture_uses_bare_newlines(self, fixtures_dir: Path) -> None:
        body = (fixtures_dir / "spreadsheet-googlesheets.csv").read_bytes()
        assert b"\r\n" not in body


class TestPublishingASpreadsheetSavedDraft:
    def test_a_saved_draft_can_be_approved_end_to_end(
        self, make_run: Any, stub_publisher: Any, fixtures_dir: Path
    ) -> None:
        """The whole point: the file the reviewer actually hands back must be publishable."""
        raw = (fixtures_dir / "spreadsheet-libreoffice-semicolon.csv").read_bytes()
        run = make_run(raw_csv=raw)
        publisher = stub_publisher()
        with pytest.raises(typer.Exit) as exc:
            run_approve(
                run_id=run.run_id,
                reject=False,
                only=None,
                dry_run=False,
                workspace=run.workspace,
                json_output=False,
                publisher=publisher,
            )
        assert exc.value.exit_code == 0
        # TC-AB12-003 is marked rejected in every fixture and must not be published.
        assert publisher.published == ["TC-AB12-001", "TC-AB12-002"]
