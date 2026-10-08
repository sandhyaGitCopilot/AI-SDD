# Contract: Draft CSV

**Feature**: [spec.md](../spec.md) | **Data model**: [data-model.md](../data-model.md)

The draft is the artifact every user of this feature touches, and the only one they are invited to edit. This contract is what the validator in `draft/validate.py` enforces and what `tests/contract/test_draft_csv_contract.py` pins.

## File

- **Name**: `testcases.csv`, inside the run directory
- **Encoding**: UTF-8 **with** BOM (`utf-8-sig`) on write. On read, accept with or without BOM (research R6)
- **Delimiter**: comma on write. On read, sniff comma vs semicolon, because some spreadsheet locales write semicolons
- **Quoting**: `QUOTE_MINIMAL` — stdlib `csv` quotes cells containing commas, quotes, or newlines
- **Line terminator**: `\r\n` on write; both accepted on read
- **Preamble**: comment lines beginning `#` above the header row, carrying provenance. Skipped on read

## Preamble

```text
# jira-testgen draft v1
# run_id: PROJ-123-20261006-142233
# source_issue: PROJ-123
# source_summary: Users can reset their password by email
# fields_read: summary, description, Acceptance Criteria
# generated_by: anthropic/claude-opus-5-5
# generated_at: 2026-10-06T14:22:33Z
# unread_content: mediaSingle (1 image not read)
# coverage_note: AC-4 not covered — case limit of 25 reached
# DO NOT EDIT the test_id column. Edit any other cell freely.
```

Satisfies FR-002 (fields read), FR-009 (coverage gaps), FR-030 (which service generated), and the staleness-judgement need in the spec's concurrent-edits edge case. It is informational: the tool reads authoritative values from `state.json`, so a user who mangles the preamble degrades their own context but cannot corrupt the run.

## Columns

Exact header spelling, in order. A missing or renamed required column fails validation with the column name (FR-016).

| # | Column | Required | Meaning |
|---|---|---|---|
| 1 | `test_id` | yes (blank allowed for user-added rows) | Stable identifier. **Must not be edited** (FR-006a) |
| 2 | `approval` | yes | `approved`, `rejected`, or `pending` (FR-015) |
| 3 | `kind` | yes | `positive`, `negative`, or `edge` (SC-005) |
| 4 | `summary` | yes | 1–255 characters |
| 5 | `preconditions` | no | May be empty |
| 6 | `steps` | yes | Numbered lines in one cell; at least one |
| 7 | `expected_results` | yes | Numbered lines in one cell; one per step, or one overall |
| 8 | `traces_to` | yes | Comma-separated criterion ids, or `REQ` |
| 9 | `jira_key` | no | Written back after publishing. Informational only |
| 10 | `notes` | no | Free text for the reviewer; never published |

### `jira_key` is informational, not authoritative

It is written back so the reviewer can see what was filed, but the duplicate guard reads `state.json`, never this column (research R4). A user who clears, copies, or reorders this column cannot cause a duplicate issue or a skipped publish. This is the one place where showing the user a value and trusting that value are deliberately separated.

## Multi-line cells

`steps` and `expected_results` hold numbered lines inside a single quoted cell:

```text
"1. Open the password reset page
2. Enter a registered email address
3. Submit the form"
```

- Write: `1. `, `2. `, … separated by `\n`
- Read: split on `\r\n`, `\r`, or `\n`; strip an optional leading `N.`, `N)`, or `-`; drop blank lines
- Order is positional, so renumbering by hand is harmless but reordering lines does reorder steps

The tolerant reader exists because a user editing in Excel will produce `\r\n` inside cells and may renumber inconsistently. Rejecting that would make the format hostile to the very workflow CSV was chosen for.

## Identifier format

`TC-<run-seq>-<nnn>` — for example `TC-7F3A-001`. Always begins with letters, so no spreadsheet coerces it to a number or date (research R6). Case-sensitive; unique within the draft.

A **blank** `test_id` means a row the user added: validation assigns a fresh identifier and publishes it as new (FR-023a). A **changed** `test_id` is indistinguishable from a new case and is treated as new — which is exactly why the preamble says not to edit it.

## Validation rules

Each produces an error naming the row number (as the user's editor numbers it, counting the preamble and header) and the column.

| # | Rule | Severity |
|---|---|---|
| V1 | All required columns present with exact spelling | fatal |
| V2 | `test_id` unique across the file | fatal — reports both rows (FR-016) |
| V3 | `approval` is one of the three allowed values, case-insensitive | fatal |
| V4 | `kind` is one of the three allowed values | fatal |
| V5 | `summary` non-empty, ≤255 characters | fatal |
| V6 | `steps` yields at least one non-empty step | fatal |
| V7 | `expected_results` count is `1` or equal to the step count | fatal |
| V8 | `traces_to` references a known criterion id, or `REQ` | warning |
| V9 | A `test_id` present in the publication record is missing from the file | warning (FR-016) |
| V10 | Row count ≤ 25 | fatal (FR-011) |
| V11 | File is readable as CSV at all | fatal — reports the byte offset |

Fatal findings block publishing and exit `10`. Warnings are printed and publishing continues. V9 is a warning rather than an error because a user deleting an already-published row is a reasonable thing to do; it must be *visible*, not forbidden.

## Round-trip guarantee

Per SC-012, a draft opened in a spreadsheet application, edited, and saved from it must still validate with step order and identifiers intact. `tests/integration/test_spreadsheet_roundtrip.py` asserts this against fixture files **saved by a real spreadsheet application**, not only files the tool wrote. A test that reads back only its own output would pass while the actual failure mode — re-encoded, re-delimited, re-quoted content — persists untested.
