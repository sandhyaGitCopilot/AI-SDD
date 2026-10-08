# Quickstart & Validation Guide: Jira Test Case Generator

**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Date**: 2026-10-06

How to run the tool and how to prove it works. Every scenario below maps to acceptance criteria in the spec, so working through them end to end is the feature's acceptance test. Interface details are in [contracts/cli.md](./contracts/cli.md) and [contracts/draft-csv.md](./contracts/draft-csv.md) rather than repeated here.

---

## Prerequisites

- Python 3.11 or newer
- A Jira Cloud site with: an issue that has a description and acceptance criteria, and a project you may create issues in. **Use a scratch project** — these scenarios create real issues
- A Jira API token (id.atlassian.com → Security → API tokens)
- An Anthropic API key
- A spreadsheet application, for the round-trip scenario

> **Before you start**: this tool sends requirement text to an external AI service, with no consent prompt and no redaction (FR-029). Use an issue you are permitted to process that way.

## Setup

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

Either copy the template and edit it:

```bash
cp .env.example .env
```

or export the four variables in your shell:

```bash
export JIRA_BASE_URL="https://your-site.atlassian.net"
export JIRA_EMAIL="you@example.com"
export JIRA_API_TOKEN="..."
export ANTHROPIC_API_KEY="sk-ant-..."
```

On Windows PowerShell use `$env:JIRA_BASE_URL = "..."`. An exported variable beats the same name in `.env`, so you can override one value without editing the file (FR-026a). `.env` is gitignored and the tool never writes to it; `.env.example` holds placeholders only. See research R8, which records why a file-based source was originally rejected and why that was reversed.

```bash
jira-testgen --help
jira-testgen drafts     # expect an empty list, exit 0
```

---

## Test suite first

```bash
pytest                                        # full suite
pytest tests/contract/ -v                     # CLI, CSV, and schema contracts
pytest tests/integration/test_resume_after_interrupt.py -v
pytest --cov=jira_testgen --cov-report=term-missing
```

No test contacts Jira or the model — Jira is mocked at the transport layer, the generation client is injected (contracts/generation.md). The suite should be runnable with no credentials set at all; if it isn't, something is reaching the network that shouldn't.

---

## Scenario 1 — Generate a draft (US1, FR-001→FR-012)

```bash
jira-testgen generate PROJ-123 --target-project QA --no-wait
```

**Expect**: existing linked test cases reported; a run directory path printed; exit `0`.

**Verify**:

- `testcases.csv` exists, with the preamble naming the fields read, the generating model, and any coverage gaps
- Opens cleanly in a spreadsheet — no mojibake, `test_id` still reads as `TC-...` and not a number or date
- Steps and expected results are legible inside their cells, in order
- At least one row has `kind` of `negative` or `edge` (SC-005)
- `traces_to` references the criteria in the source issue (FR-007)
- **Jira is untouched**: no new issues, no new links on `PROJ-123` (FR-013, SC-006)

## Scenario 2 — Review, edit, approve (US2, FR-014→FR-016)

Edit the draft: reword a summary, add a step, set `approval` to `approved` on a few rows and `rejected` on one, and add a row by hand leaving `test_id` blank.

```bash
jira-testgen approve --dry-run     # validate and preview, touching nothing
jira-testgen approve
```

**Expect**: only approved rows published; the hand-added row published with a freshly assigned identifier (FR-023a); each created issue key and URL printed (FR-024).

**Verify in Jira**: each test case exists with its edited wording — proving the user's edits were published, not the generated originals (US2 scenario 2) — and each is linked to `PROJ-123`, visible from both sides (FR-020).

## Scenario 3 — Rejection leaves Jira untouched (FR-015, SC-006)

```bash
jira-testgen generate PROJ-124 --no-wait
jira-testgen approve --reject
```

**Expect**: exit `0`, nothing created, draft retained for later reuse.

## Scenario 4 — Resume after interruption (FR-017, SC-010)

```bash
jira-testgen generate PROJ-125 --target-project QA   # wait at the prompt, then Ctrl+C
jira-testgen drafts                                   # the run is listed as pending
jira-testgen approve PROJ-125-<timestamp>
```

**Expect**: publishing proceeds with no second Jira read and no regeneration. Confirm from the run log that no generation request was made.

## Scenario 5 — No duplicates after a partial publish (FR-023, SC-007)

The important one. Approve a draft and interrupt partway through publishing (Ctrl+C after a few issues appear), then:

```bash
jira-testgen approve <run-id>
```

**Expect**: already-created cases skipped; only the outstanding ones created. Count issues in Jira — the total must equal the approved case count, with no duplicate summaries.

Then re-run `approve` on the now-complete draft: it should report the draft already published and create nothing (US3 scenario 3).

For the harder variant, edit a published row's summary in the CSV and re-approve. It must **not** create a second issue — identity comes from `test_id`, not the summary text (research R4).

## Scenario 6 — Spreadsheet round trip (SC-012, FR-012b)

Open the draft in Excel or LibreOffice, change a cell, and save **in CSV format**, accepting any format warning.

```bash
jira-testgen approve --dry-run
```

**Expect**: validation passes; step order and identifiers intact. This is the scenario that catches re-encoding, delimiter changes, and identifier coercion — the known costs of the CSV choice (research R6).

## Scenario 7 — Broken draft is rejected clearly (FR-016)

Delete the `expected_results` column header, duplicate a `test_id`, and blank a `summary`.

```bash
jira-testgen approve
```

**Expect**: exit `10`, nothing published, and each problem reported by row number and column name — including both row numbers for the duplicated identifier. Fix the file and re-approve without regenerating.

## Scenario 8 — Error paths (SC-008)

Walk every one. SC-008 requires each to name what went wrong and what to do next, so read the messages, don't just check the codes.

| Command | Expected exit |
|---|---|
| `jira-testgen generate NOTAKEY` | `2` |
| `jira-testgen generate PROJ-999999` | `3` |
| `generate` against an issue you cannot read | `4` |
| `JIRA_API_TOKEN=bad jira-testgen generate PROJ-123` | `5` |
| `generate` against an issue with an empty description and no criteria | `6`, and **no draft written** |
| `--target-project` where you lack create rights | `7`, before anything is created |
| `ANTHROPIC_API_KEY=bad jira-testgen generate PROJ-123` | `8`, naming the generation service, not Jira |
| `jira-testgen approve` with no pending drafts | `9` |
| `--ac-field "No Such Field"` | `13`, listing available fields |

## Scenario 9 — No credential leakage (FR-027)

```bash
jira-testgen generate PROJ-123 --no-wait 2>&1 | tee run.log
grep -F "$JIRA_API_TOKEN" run.log testcases.csv state.json   # expect no matches
grep -F "$ANTHROPIC_API_KEY" run.log testcases.csv state.json # expect no matches
```

Also check the per-run log and confirm `.env` is untouched and uncommittable:

```bash
RUN=$(ls -td .jira-testgen/runs/* | head -1)
grep -F "$JIRA_API_TOKEN" "$RUN/run.log" "$RUN/state.json"   # expect no matches
git check-ignore -v .env                                      # expect a .gitignore match
git check-ignore .env.example || echo "template is committable, as intended"
md5sum .env && jira-testgen drafts >/dev/null && md5sum .env  # expect identical hashes
```

Repeat with a deliberately invalid token so an error path with auth headers is exercised — that traceback is the realistic leak, not ordinary output. Run it once with the token exported and once with it only in `.env`, since FR-026b requires both sources to be redacted identically.

## Scenario 10 — Scripted use (FR-028)

```bash
jira-testgen generate PROJ-123 --no-wait --json | jq -r '.run_id'
jira-testgen approve --yes --json | jq -r '.created[].issue_key'
echo "exit=$?"
```

**Expect**: parseable JSON on stdout with no decorative output mixed in, and exit codes that let a script branch on the outcome.

---

## Acceptance coverage map

| Spec item | Scenario |
|---|---|
| US1 — draft from a requirement | 1 |
| US2 — review, edit, approve | 2, 3, 7 |
| US3 — publish as linked issues | 2, 5 |
| FR-004 insufficient content | 8 |
| FR-011 case cap | 1 (preamble note) |
| FR-016 draft validation | 7 |
| FR-017 / SC-010 resume | 4 |
| FR-022 permission precheck | 8 |
| FR-023 / SC-007 no duplicates | 5 |
| FR-024 result reporting | 2 |
| FR-027 no credential leakage | 9 |
| FR-028 scripted use | 10 |
| FR-031 generation failure | 8 |
| SC-005 negative/edge proportion | 1 |
| SC-006 untouched on rejection | 1, 3 |
| SC-008 all error paths | 8 |
| SC-012 spreadsheet round trip | 6 |

Not covered by a single command, because they are measured over a sample of real use rather than asserted once: SC-001 and SC-002 (timing), SC-003 (edit rate), SC-009 (tester comprehension), SC-011 (review time at full size). Record these during the first week of real use.

## Cleanup

Delete the issues created in the scratch project, and remove the run directories:

```bash
rm -rf .jira-testgen/runs/PROJ-12*
```
