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
quoted cells, which is Excel's actual behaviour and the detail most hand-written fixtures
get wrong), and quoting style. They were not produced by running Excel, LibreOffice or
Google Sheets, because no spreadsheet application was available in the environment that
generated them.

That is a weaker guarantee than `contracts/draft-csv.md` asks for. If you have the
applications to hand, the right thing to do is:

1. Open `tests/fixtures/spreadsheet-excel-windows.csv` in the real application.
2. Save it back, unchanged, in that application's default CSV format.
3. Commit the result over the fixture.

The test will then be asserting against genuine spreadsheet output. Nothing in the test
needs to change — it reads whatever bytes are in these files.

`make_fixtures.py` is not checked in on purpose: these are fixtures, not generated
artifacts, and regenerating them would defeat the point of replacing them with real saves.
