# jira-testgen

Generate manual test cases from a Jira requirement, review them, and publish them back to Jira as linked issues.

> **Status**: all three user stories are implemented — draft generation, the review gate, and publishing as linked issues. Not yet exercised against a real Jira site; see [Before you trust it](#before-you-trust-it).

## Requirement text is sent to an external AI service

This tool sends the description and acceptance criteria of the Jira issue you name to
Anthropic's API, in order to generate test cases from them.

There is **no consent prompt** and **no redaction**. The tool does not ask before sending
and does not attempt to strip names, customer details, credentials, or anything else that
happens to be in the issue. Running `generate PROJ-123` sends that issue's text.

Organizational approval for that processing is assumed as a precondition of use (FR-029).
If you do not have it, do not run this on real issues. The tool cannot tell the difference
and will not stop you.

Jira credentials are never sent to the AI service, and no credential of either kind is
written to the draft, the run state, the logs, or any error message (FR-027) — including
one that came from a `.env` file.

## Install

Python 3.11 or newer.

```bash
python -m venv .venv
.venv/Scripts/activate       # Windows
# source .venv/bin/activate  # macOS / Linux
pip install -e ".[dev]"
```

## Configure

Credentials resolve in one order, highest first: **CLI option → environment variable →
`.env` file → default**. An exported variable always beats the file, so a stale `.env`
cannot silently override a token you just exported.

| Variable | Required | Purpose |
|---|---|---|
| `JIRA_BASE_URL` | yes | e.g. `https://acme.atlassian.net` |
| `JIRA_EMAIL` | yes | Account email for the API token |
| `JIRA_API_TOKEN` | yes | From id.atlassian.net → Security → API tokens |
| `ANTHROPIC_API_KEY` | yes, for `generate` | Generation service key. `approve` and `drafts` do not need it |
| `JIRA_TARGET_PROJECT` | no | Defaults to the source issue's project |
| `JIRA_AC_FIELD` | no | Acceptance criteria field name; defaults to reading the description |
| `JIRA_ISSUE_TYPE` | no | Issue type for created test cases (default `Task`) |
| `JIRA_LINK_TYPE` | no | Link relationship (default `Relates`) |

### Option 1 — a `.env` file (persists across terminals)

```bash
cp .env.example .env     # then edit it
```

`.env` is gitignored; `.env.example` holds placeholders only and is the file that ships.
The tool only ever **reads** `.env` — it never creates, rewrites or appends to it, and it
does not export its values into the environment of programs it launches.

The tradeoff, stated plainly: this puts your API token in plaintext on disk. The ignore
rule stops the ordinary accident, but a copied directory, a container image layer, or a
`git add -f` will still carry it out. It is not *more* secure than exporting — exporting
writes the token to your shell history, which is also plaintext — so pick whichever
exposure you would rather manage.

A `.env` that exists but has a line that is not `NAME=value` is an error, not something
silently ignored: the tool names the file and the line number, and never prints the line's
contents, since that content is the secret. Blank lines and `#` comments are fine.

### Option 2 — export per shell session

```bash
export JIRA_BASE_URL="https://acme.atlassian.net"
export JIRA_EMAIL="you@example.com"
export JIRA_API_TOKEN="..."
export ANTHROPIC_API_KEY="sk-ant-..."
```

PowerShell: `$env:JIRA_BASE_URL = "..."`. These last only for that terminal, which is the
friction `.env` exists to remove.

You need read access to the source issue, and `CREATE_ISSUES` plus `LINK_ISSUES` on the
target project. Both are checked before a model call is spent, so a missing permission
costs you seconds rather than money.

## The three commands

### `generate <ISSUE-KEY>` — read the requirement, draft the test cases

```bash
jira-testgen generate PROJ-123 --target-project QA
```

Reads the issue, reports any test cases already linked to it, generates up to 25 cases,
writes `testcases.csv` into a run directory, and then **stops and waits** for you to
review it. Nothing has been created in Jira at this point.

| Option | Meaning |
|---|---|
| `--target-project` | Project to file test cases in. Defaults to the source project |
| `--ac-field` | Acceptance criteria field name |
| `--issue-type` | Issue type for created test cases |
| `--link-type` | Link relationship name |
| `--max-cases` | Cap, 1–25. A value above 25 is rejected, not silently clamped |
| `--workspace` | Run directory root (default `.jira-testgen`) |
| `--no-wait` | Write the draft and exit instead of waiting at the prompt |
| `--yes` | Publish without review. **Bypasses the safety gate** |
| `--json` | Machine-readable result on stdout |

### `approve [RUN-ID]` — review the draft and publish

```bash
jira-testgen approve --dry-run     # report what would be created; touch nothing
jira-testgen approve               # publish the rows marked `approved`
jira-testgen approve --reject      # discard the draft; create nothing
```

Re-reads the CSV **from disk**, so your edits are what gets published. With no run id it
acts on the only pending draft, and refuses to guess when there are several.

| Option | Meaning |
|---|---|
| `--reject` | Mark the draft rejected; create nothing. Exits `0` |
| `--only` | Comma-separated `test_id` list. Naming a case approves it |
| `--dry-run` | Validate and report; touch nothing in Jira |
| `--yes` | Publish everything not marked `rejected`, ignoring the approval column |
| `--json` | Machine-readable result |

### `drafts` — list drafts awaiting review

```bash
jira-testgen drafts
```

Run id, issue, phase, case count, published count, and age. Exits `0` on an empty list —
an empty list is an answer.

## Editing the draft

`testcases.csv` is yours to edit, in a spreadsheet or a text editor. Ten columns:
`test_id`, `approval`, `kind`, `summary`, `preconditions`, `steps`, `expected_results`,
`traces_to`, `jira_key`, `notes`.

- Set `approval` to `approved` on the rows you want published, `rejected` on the rest.
- Edit any cell freely. Add rows by hand — leave `test_id` **blank** and one is assigned.
- **Do not edit `test_id`.** It is how the tool knows what it has already published. A
  changed identifier is indistinguishable from a new case and will be filed as one.
- `jira_key` is filled in after publishing, for your information only. Clearing it,
  sorting it or inventing a value cannot cause a duplicate or a skipped publish.
- `notes` is for you. It is never published to Jira.

Steps and expected results are numbered lines inside one cell. `expected_results` must
hold either one entry per step, or exactly one overall. Renumbering by hand is harmless;
reordering lines does reorder the steps.

## The safety gate

Nothing reaches Jira without a human saying yes. `generate` stops at a review prompt;
`approve` publishes only what the approval column (or `--only`, or `--yes`) selects.
Approving nothing is a success, not an error — it exits `0` and says nothing was
published.

`--yes` bypasses the review, announces that it is doing so, and still runs validation. In
`--json` mode without `--yes` there is nobody to prompt, so the run stops at the draft and
the payload says `awaiting_review: true`.

## If it is interrupted

Publishing records each created issue the instant it succeeds. Re-running `approve` on the
same run creates only the cases that are still outstanding, and repairs any issue that was
created but never linked. Re-approving a fully published draft creates nothing.

This holds even if you edit a published case's summary first: identity comes from
`test_id` in `state.json`, not from the text.

Each run directory also holds `run.log`, one JSON object per line, with credentials
scrubbed — the place to look after a run that failed partway.

## Exit codes

Scripted callers can branch on these; they are a contract.

| Code | Meaning |
|---|---|
| `0` | Success, including "nothing to publish" and an empty `drafts` list |
| `1` | Unexpected internal error |
| `2` | Malformed issue key or invalid arguments |
| `3` | Issue not found |
| `4` | No permission to read the issue |
| `5` | Authentication failure |
| `6` | Requirement content empty or too sparse |
| `7` | Missing `CREATE_ISSUES` or `LINK_ISSUES` on the target project |
| `8` | Generation service unreachable, refused, or unusable output |
| `9` | No pending draft, or an ambiguous run selection |
| `10` | Draft failed validation |
| `11` | Publish partially or wholly failed; re-runnable |
| `12` | Retries exhausted against Jira |
| `13` | Site configuration unresolved (unknown field, issue type, or link type) |

`3`, `4`, and `5` are deliberately distinct: a typo in an issue key and an expired token
are different problems.

## Before you trust it

The automated suite is thorough and passes with no credentials set, but it mocks Jira at
the transport layer — **no code path here has run against a real Jira site**. Before
relying on this, work through
[`quickstart.md`](specs/001-jira-test-case-generator/quickstart.md) against a **scratch
project**; those scenarios create real issues.
[`docs/quickstart-verification.md`](docs/quickstart-verification.md) tracks which have
been done.

## Develop

```bash
pytest                                    # full suite; passes with no credentials set
pytest --cov=jira_testgen --cov-report=term-missing
ruff check . && ruff format --check .
mypy src/jira_testgen
```

No test contacts Jira or the model: Jira is mocked with `respx`, and the generation client
is injected. If a test needs credentials to pass, something is reaching the network that
should not be.

Design documents live in [`specs/001-jira-test-case-generator/`](specs/001-jira-test-case-generator/):
[spec](specs/001-jira-test-case-generator/spec.md),
[plan](specs/001-jira-test-case-generator/plan.md),
[research](specs/001-jira-test-case-generator/research.md) (the why behind the awkward
decisions), and [contracts](specs/001-jira-test-case-generator/contracts/).
