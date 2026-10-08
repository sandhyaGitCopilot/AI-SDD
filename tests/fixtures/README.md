# Draft CSV fixtures

These files back `tests/integration/test_spreadsheet_roundtrip.py` (T043) and the SC-012
round-trip guarantee in `contracts/draft-csv.md`.

They all carry **the same three test cases**. What differs is only how the file was
encoded — which is the whole point. A round-trip test that reads back a file the tool
itself wrote would pass while the real failure mode (a spreadsheet re-encoding, re-delimiting
and re-quoting the draft on save) stayed untested.

| File | Represents | Encoding | Delimiter | Line endings | Quoting |
|---|---|---|---|---|---|
| `spreadsheet-excel-windows.csv` | Excel for Windows, "CSV UTF-8" save | UTF-8 **with** BOM | `,` | `\r\n`, including inside quoted cells | minimal |
| `spreadsheet-libreoffice-semicolon.csv` | LibreOffice Calc in a European locale | UTF-8 with BOM | `;` | `\r\n` | every field quoted |
| `spreadsheet-googlesheets.csv` | Google Sheets → Download → CSV | UTF-8, **no** BOM | `,` | `\n` | minimal |
| `spreadsheet-hand-renumbered.csv` | A reviewer renumbering steps by hand after a save | UTF-8 with BOM | `,` | `\r\n` | minimal |

## Provenance — read this before trusting them

These four files were **authored to reproduce, byte for byte, the output each application
produces** — BOM presence, delimiter, terminator placement (including `\r\n` *inside*
quoted cells, believed at the time to be Excel's behaviour — see the correction below), and
quoting style. They were not produced by running Excel, LibreOffice or Google Sheets,
because no spreadsheet application was available in the environment that generated them.

That is a weaker guarantee than `contracts/draft-csv.md` asks for. If you have the
applications to hand, the right thing to do is:

1. Open `tests/fixtures/spreadsheet-excel-windows.csv` in the real application.
2. Save it back, unchanged, in that application's default CSV format.
3. Commit the result over the fixture.

The test will then be asserting against genuine spreadsheet output. Nothing in the test
needs to change — it reads whatever bytes are in these files.

`make_fixtures.py` is not checked in on purpose: these are fixtures, not generated
artifacts, and regenerating them would defeat the point of replacing them with real saves.

---

## Partial verification against real Excel — 2026-10-08 (T089)

An Excel was found on the development machine and `spreadsheet-excel-windows.csv` was
round-tripped through it via COM automation. **The fixture was deliberately not replaced**,
and here is why, along with what the exercise established.

### Why the fixture was not replaced

The Excel available is **version 12.0 — Excel 2007**, which predates the `xlCSVUTF8`
(`SaveAs` format `62`) "CSV UTF-8 (Comma delimited)" option that this fixture's row in the
table above documents; that format arrived in Excel 2016. Asking 2007 for format `62` does
not fail, it silently falls back to plain CSV. Committing that output would mislabel the
fixture: the file would claim to be a "CSV UTF-8" save and not be one.

**So this fixture still needs a real save from Excel 2016 or newer**, and the three-step
procedure above still stands. LibreOffice Calc is not installed on this machine, and the
Google Sheets fixture needs a browser round trip, so both of those remain unverified too.

### What real Excel 2007 actually did

| Property | What this fixture asserts | What Excel 2007 produced |
|---|---|---|
| BOM | present | **absent** (format `62` unsupported, fell back) |
| Row terminators | `\r\n` | `\r\n` ✅ |
| Newlines *inside* quoted cells | `\r\n` | **bare `\n`** |
| `#` preamble lines | as written | **padded with 9 trailing commas** to the 10-column width |
| `test_id` | `TC-AB12-001` survives | **survives, unchanged** ✅ |

Two of these are worth carrying forward. The claim that Excel writes `\r\n` inside quoted
cells — called out above as "the detail most hand-written fixtures get wrong" — **did not
hold**: this Excel normalises in-cell newlines to bare `\n` while keeping `\r\n` between
rows. And Excel pads every short row out to the used column count, so the comment preamble
comes back with trailing commas. Neither was anticipated.

### The part that matters: the reader coped

Excel's genuine output was fed through `draft.csv_io.read_draft_records`, which recovered
all three test cases with **step order, step counts, and identifiers intact** — despite the
missing BOM, the in-cell `\n`, and the comma-padded preamble.

That is direct evidence for SC-012 against real spreadsheet output, which is what this
directory exists to produce. It is narrower than a full fixture replacement, and it is
recorded here rather than asserted in a test, because a test pinned to one machine's Excel
version is not a test anybody else can run.

> **Do not "fix" the line endings in these files.** `.gitattributes` marks
> `tests/fixtures/*.csv` as `-text` for a reason: Git's EOL normalization had already
> stripped the `\r\n` from three of these fixtures in the repository and was rewriting the
> bare-LF Google Sheets fixture to CRLF on every Windows checkout, which made the suite
> pass against bytes no spreadsheet ever wrote. See that file's comments.
