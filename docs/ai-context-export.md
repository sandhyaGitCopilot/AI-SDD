# Project & Working-Context Export — `my-sdd` / `jira-testgen`

**Exported**: 2026-10-08 · **For**: transferring context to another AI provider
**Source project root**: `C:\project\Calude-Project\my-sdd` (Windows 11 Pro, PowerShell primary)

## How to read this file

Three confidence tiers, kept strictly separate:

- **CONFIRMED MEMORY** — sourced from a file in the project, a config file, or the user's own
  recorded prompts. Every item here is traceable to a cited artifact.
- **INFERRED CONTEXT** — reasonable deductions from repeated patterns. Not stated by the user.
- **UNCERTAIN INFORMATION** — open questions, unverified claims, and things deliberately not known.

**Provenance note, stated up front**: this project has **no persistent assistant-memory store and no
`CLAUDE.md`**. The memory directory
(`~/.claude/projects/C--project-Calude-Project-my-sdd/memory/`) is **empty**, and no `CLAUDE.md`
exists anywhere under `C:\project\Calude-Project`. So there is no curated "remembered preferences"
list to export. Everything below was reconstructed from: the project's own spec-driven-development
artifacts, its config files, the user's recorded prompt history for this directory, and the user's
own authored skills. Nothing has been invented.

---

# CONFIRMED MEMORY

## 1. Project identity and goal

| Field | Value |
|---|---|
| Package name | `jira-testgen` (version `0.1.0`) |
| One-line goal | "Generate manual test cases from a Jira requirement, review them, and publish them back as linked issues." |
| Feature id | `001-jira-test-case-generator` |
| Spec created | 2026-10-06 |
| Interface | Single Python CLI, three commands: `generate`, `approve`, `drafts` |
| Entry point | `jira-testgen = "jira_testgen.cli:app"` |
| Version control | **Not a git repository** (no `.git` in the project root) |

*Source: `pyproject.toml`, `specs/001-jira-test-case-generator/spec.md`, `.specify/feature.json`.*

**The original request, verbatim from the user's first prompt in this directory:**

> Build an automated test case generator that integrates with Jira via REST API.
>
> Key Requirements:
> 1. Requirement Ingestion: Fetch the description and acceptance criteria of a given Jira Issue Key (e.g., PROJ-123) using Jira Cloud REST API.
> 2. Test Generation: Generate structured manual test cases (Summary, Preconditions, Steps, Expected Results) covering edge cases and negative scenarios.
> 3. Human-in-the-Loop Review: Save draft test cases and halt execution until the user manually approves or edits the file in the CLI interface.
> 4. Output Action: Once approved, create the test cases in Jira as linked issue items using the REST API.

## 2. Working method — Spec-Driven Development via Spec Kit

The user drives this project exclusively through **Spec Kit (`speckit`) slash commands**, in strict
phase order. This is the single most important thing to carry to another provider.

**Spec Kit configuration** (`.specify/init-options.json`, `.specify/integration.json`):

```json
{ "ai": "claude", "ai_skills": true, "feature_numbering": "sequential",
  "here": false, "integration": "claude", "script": "py", "speckit_version": "1.0.13" }
```

- Helper scripts are **Python** (`"script": "py"`), not shell — and `plan.md` records that Python
  3.11+ was chosen partly *to match this*, so contributors need one toolchain, not two.
- Features are numbered sequentially (`001-…`).

**Actual command sequence the user issued, in order** (from recorded prompt history for this directory):

1. `/speckit-specify <the four key requirements above>`
2. `/speckit-clarify` → answered the five questions with `B`, `C`, `A`, `D`, then `yes`
3. `/speckit-plan`
4. `/speckit-tasks`
5. `/speckit-implement T001 - T0039`
6. `/speckit-implement T040 - T053`
7. `/speckit-implement T054 - T066`
8. `/speckit-implement T067 - T074`
9. `/speckit-clarify why .env file is missing this is needed to provide JIRA secret details please check` → answered `A`
10. `/speckit-tasks` (regenerate, adding Phase 7)
11. `/speckit-implement T075 - T088`
12. `/speckit-implement T073`
13. `/remote-control SDD` (used repeatedly — the user runs these sessions under Remote Control, session name "SDD")

**Confirmed behavioural rule: the user implements in explicit, named task ID ranges**
(`T001 - T0039`, `T040 - T053`, …), not "implement everything". They batch work deliberately and
re-run `/speckit-tasks` when the spec changes rather than hand-editing `tasks.md`.

## 3. Recurring requirements (non-negotiables, each with its requirement ID)

These appear across `spec.md`, `plan.md`, `research.md`, `contracts/`, and `README.md`. They are the
project's standing constraints.

| Requirement | Rule | ID |
|---|---|---|
| **Human-in-the-loop gate** | Nothing reaches Jira without explicit human approval. `generate` halts at a prompt; `approve` publishes only the selected subset. Approving nothing is success (exit `0`), not an error. | FR-013, FR-015, FR-018, SC-006 |
| **No credential ever in output** | No secret may appear in the draft, `state.json`, `run.log`, stdout/stderr, or a traceback. Enforced by a `Secret` type with redacted `__repr__`/`__str__` plus a root-logger redaction filter keyed on the live values. Asserted in tests. | FR-027, research R8 |
| **Exit codes are a contract** | 14 distinct codes (`0`–`13`). Adding one is treated as a breaking change — Phase 7 explicitly reused exit `2` rather than adding a code. `3`/`4`/`5` must stay distinguishable (bad key vs. no permission vs. bad credentials). | FR-028, SC-008, `contracts/cli.md` |
| **25 test cases per run, hard cap** | Enforced *in code* after parsing, not only in the prompt — "a prompt instruction is a request, not a constraint". A `--max-cases` above 25 is **rejected, not silently clamped**. | FR-011, research R7 |
| **Duplicate-free publishing** | Authoritative record is `state.json`, keyed by `test_id`, written atomically (temp file + `os.replace`) **immediately after each create**, before the link. Never a CSV column. | FR-023, SC-007, research R4 |
| **Stable identifier is identity** | `test_id` (format `TC-<run-seq>-<nnn>`) — not summary text, not row position — identifies a case for life. Users are told not to edit it; a blank one gets assigned. | FR-006a, FR-023a |
| **Environment beats `.env`** | Resolution order: CLI option → env var → `.env` → default. A stale file must never override a fresh export. | FR-026a |
| **The tool never writes `.env`** | Read-only. Never creates, appends, truncates, or logs it. Asserted by byte-comparing the file before/after a run. A malformed line reports file + 1-based line number and **never quotes the value** (the value is the secret). `.env.example` ships with placeholders only. | FR-026b, FR-026c |
| **No test touches the network** | Jira is mocked with `respx` at the transport layer; the generation client is injected. The suite must pass with no credentials set **and** with a `.env` present. "If a test needs credentials to pass, something is reaching the network that should not be." | T072, T075, T088 |
| **Draft must survive a spreadsheet round trip** | `utf-8-sig` (BOM), `QUOTE_MINIMAL`, `\r\n`, non-numeric `test_id` prefix so Excel can't coerce it to a number/date. Reader tolerates BOM-or-not and sniffs `,` vs `;`. | FR-012a/b, SC-012, research R6 |
| **Coverage shortfalls are disclosed, not hidden** | Uncovered criteria, unread ADF node types, and cap-reached are all named in the draft. | FR-009, SC-004 |
| **Negative and edge cases are mandatory** | Not just the happy path; SC-005 targets ≥30% negative/edge per run. | FR-008, SC-005 |
| **Permission precheck before any write** | `GET /mypermissions` with `projectKey` + `permissions=CREATE_ISSUES,LINK_ISSUES`. Missing `LINK_ISSUES` is a hard stop, not a degraded mode. | FR-022, research R2 |
| **Retries bounded** | 5 attempts max. Honour `Retry-After`; else jittered exponential backoff (`1s × 2^attempt`). **Writes are not retried** on a bare 5xx or connection error — that risks the duplicates SC-007 forbids. | FR-025, research R3 |

## 4. Clarification decisions (the user's own recorded answers)

### Session 2026-10-06 (`/speckit-clarify`)

| Question | User's decision |
|---|---|
| May requirement text leave the machine for an external AI service? | **Yes** — external AI generation, **no in-tool consent step**; organizational approval is assumed a precondition of use. |
| Does review wait at a prompt, or exit and resume later? | **Both** — waits interactively by default, *and* draft + run state survive so a separate `approve` command can finish an abandoned run. |
| What makes a test case "the same one" across edits? | **A stable identifier assigned at generation**; user is told not to change it; hand-added cases get a new one. |
| What format is the draft file? | **CSV** — one row per test case, editable in a spreadsheet or a text editor. |
| Max test cases per run? | **25**. |

### Session 2026-10-07 (`/speckit-clarify` — the `.env` correction)

| Question | User's decision |
|---|---|
| Load credentials from a `.env` file in the working directory? | **Yes** — load if present, real environment variables take precedence, file is read-only/never logged, values go through the same redaction path, and a `.env.example` with placeholders ships. |

## 5. Corrections the user made

**One explicit correction is on record**, and it reversed a documented design decision:

> `/speckit-clarify why .env file is missing this is needed to provide JIRA secret details please check`

Research section **R8 had rejected all file-based credential sources**. The user pushed back because
re-entering four credentials in every new terminal was the actual friction. R8 was formally **amended
(2026-10-07) rather than rewritten**, with the reversal argued on three grounds recorded in
`research.md`:

1. The original objection ("a token in a working-directory file gets committed") was already
   addressed by a `.gitignore` rule that did not exist when the decision was written.
2. The spec's own Assumptions already permitted "a local configuration the user controls" — so the
   original decision was *narrower than the spec it served*, an inconsistency rather than a
   deliberate tightening.
3. The alternative in practice is `export JIRA_API_TOKEN=…`, which writes the token to shell history
   in plaintext — "not safer; the same exposure somewhere users think to look less often."

This produced an entire new **Phase 7 (T075–T088)**, and the residual risk was **recorded in the
spec's Assumptions rather than claimed away**.

**Transferable lesson**: when this user challenges a decision, the expected response is to re-open the
decision record, state the reversal explicitly with reasons, keep the superseded reasoning visible,
and regenerate the task list — not to silently change the code.

## 6. Tools and technologies

**Runtime stack** (`pyproject.toml`):

- Python **3.11+** (`requires-python = ">=3.11"`); build backend **hatchling**; `src/` layout
- `typer>=0.12` (CLI), `httpx>=0.27` (Jira REST), `pydantic>=2.7` (schemas), `anthropic>=0.40`
  (generation), `rich>=13.7` (prompts/tables), stdlib `csv` (deliberately no third-party CSV lib)
- **No `python-dotenv`** — the `.env` parser was hand-written on purpose: "the format is a few lines
  of parsing and adding `python-dotenv` for it would enlarge the dependency surface for no gain" (T080)

**Dev / quality stack**:

- `pytest>=8.0`, `pytest-cov>=5.0`, `respx>=0.21`, `ruff>=0.5`, `mypy>=1.10`
- **ruff**: `line-length = 100`, `target-version = "py311"`, `select = ["E","F","W","I","N","UP","B","SIM","RUF"]`,
  `ignore = ["E501", "N818"]`, `extend-exclude = [".specify"]` ("Spec Kit's own vendored scripts;
  not ours to restyle")
- **mypy**: `strict = true`, `warn_unreachable = true`, on `src/jira_testgen` only; `ignore_missing_imports`
  for `anthropic.*` and `respx.*`
- **pytest**: `testpaths = ["tests"]`, `addopts = "--strict-markers -ra"`, registered markers
  `contract` / `integration` / `unit`; coverage with `branch = true`, `show_missing = true`
- Standard check command set, from `README.md`:
  `pytest` · `pytest --cov=jira_testgen --cov-report=term-missing` ·
  `ruff check . && ruff format --check .` · `mypy src/jira_testgen`

**The `N818` ignore is a deliberate, documented naming decision**: exception classes are named for the
condition they report (`IssueNotFound`, `AuthFailure`), one-to-one with the exit codes in
`contracts/cli.md`, because "reading them as conditions is what makes that mapping legible."

**Generation model choice** (research R7): model **`claude-opus-5-5`**, adaptive thinking
(`thinking: {"type": "adaptive"}`), explicit `output_config: {"effort": "high"}`, **streaming**, and
**structured outputs** (`output_config.format`) carrying a JSON schema. Rationale on record:

- Opus's effort default is `medium`, one level below the rest of the family, so leaving it unset would
  "quietly under-resource a task that benefits from careful reasoning."
- Cheaper models (`claude-sonnet-5-5`, `claude-haiku-4-5`) were **explicitly rejected as the default**:
  "Test case quality is the product here… Downgrading to save per-run cost trades the feature's main
  quality metric for pennies, and that is the user's call to make explicitly, not a default to assume."
- Forced tool use to coerce JSON was rejected (returns 400 on this model); prompt-only "return JSON"
  with hand-rolled parsing was rejected as strictly worse.

**Environment**: Windows 11 Pro primary, PowerShell shell, Python 3.11 configured / 3.12 used in the
verification run. `plan.md` states Windows being primary "makes CSV encoding, `\r\n` line endings, and
Excel round-tripping first-class concerns rather than afterthoughts."

## 7. Architecture decisions worth carrying over

Four from `research.md`, all load-bearing:

- **R1 — ADF both directions (Verified)**: Jira Cloud v3 requires Atlassian Document Format for body
  fields; a plain string returns HTTP 400. So `jira/adf.py` owns an outbound builder *and* an inbound
  text extractor. Supported nodes: `paragraph`, `text`, `heading`, `bulletList`, `orderedList`,
  `listItem`, `codeBlock`, `blockquote`, `table`, `hardBreak`, `rule`. Everything else
  (`mediaSingle`, `mediaGroup`, `inlineCard`, `embedCard`, `extension`) is collected into an
  unread-node-types list and reported — **never silently dropped**. Using the older v2 API for its
  wiki-markup strings was rejected.
- **R4 — state separate from the CSV**: the single most important rejection in the design. Published
  issue keys may **not** live in a CSV column, because FR-014 invites the user to edit that file and
  spreadsheets rewrite it on save. "The one record that must survive cannot live in the file the user
  is told to edit freely."
- **R5 — runtime discovery, never hardcoding**: AC field (`GET /field`), link type
  (`GET /issueLinkType`), and issue type (validated against project create-meta) are all resolved at
  runtime and verified *before generation* — so a misconfigured site fails before a model call is
  spent, and the error names what was searched for and what exists. Hardcoded `customfield_10000`-style
  IDs and a hardcoded `"Relates"` name were both rejected.
- **Storage**: filesystem only, no database. One directory per run under a configurable workspace root
  (default `.jira-testgen/runs/<issue-key>-<timestamp>/`) holding `testcases.csv` + `state.json` +
  `run.log` (one JSON object per line, credentials scrubbed).

**One documented deviation from the plan**, made in `tasks.md` for story independence: the plan put the
permission precheck and issue creation together in `jira/writer.py`; they were split into
`jira/permissions.py` (US1, runs before generation) and `jira/writer.py` (US3, creates and links) so
US1 and US3 never edit the same file. The source tree confirms both files exist.

## 8. Project status as of this export

- **87 of 88 tasks in `tasks.md` are `[X]`; T073 is `[~]` (partial)** — see the blockers below. All
  three user stories are implemented. A `## Phase 8: Convergence` section (T089–T093) was appended by
  `/speckit-converge` on 2026-10-08 recording the remaining work.
- **Test suite: 753 passed** (re-run 2026-10-08; the 746 figure in `docs/quickstart-verification.md`
  predates the T073 regression tests). Verified twice at the time — once with no credentials and no
  `.env`, once with no credentials and a `.env` present (the T088 double-check). `ruff` clean, `mypy`
  clean under strict.
- **Verified against a live Jira site** (`data-dc.atlassian.net`, recorded 2026-10-07) — but **only the
  read-only half**:
  - Scenario 8 error paths: **9 of 9** executed against real Jira (exits 2, 2, 3, 5, 13, 13, 13, 0, 9)
  - Scenario 9 credential leakage: 18 output streams/artifacts searched for three real token values —
    **no credential appeared in any of them**; `.env` byte-identical before and after
  - Real ADF ingestion against `OHRM-9` / `OHRM-7`; FR-009 confirmed on real data (an `inlineCard`
    smart link was correctly reported as unread rather than dropped)
  - Site facts resolved on that site: link type `Relates` (inward "relates to"), issue type `Task`
    id `10051`; `mypermissions` on `OHRM` returned `CREATE_ISSUES: true`, `LINK_ISSUES: true`;
    visible projects `OHRM`, `SCRUM`, `TC`
- **README states plainly**: "no code path here has run against a real Jira site" for the write paths,
  and directs the reader to work through `quickstart.md` against a **scratch project** first.

### A real defect was found and fixed by that live run

**A bad token reported a configuration error (exit 13) instead of an auth failure (exit 5).** On
Atlassian Cloud, most read endpoints do **not** return 401 for an invalid token — they serve the
request anonymously and return `200` with an empty result. Verified on the site:
`/rest/api/3/issueLinkType` returned `200` with **0 link types** on a bad token, while
`/rest/api/3/myself` returned `401`. Because site config is resolved before the issue fetch, the user
was told to go configure link types when their token was simply wrong.

**Fix**: `jira/permissions.py` gained `verify_authentication()` — a single `GET /myself` probe that runs
*before* site resolution, treating both a `401` and a `200`-with-no-account as exit 5. Regression tests
live in `tests/integration/test_generate_failures.py::TestAnonymousResponsesAreTreatedAsAuthFailures`.

### Two site behaviours recorded as worth knowing

- **This site rejects unbounded JQL** ("Unbounded JQL queries are not allowed here."). The
  reconciliation queries in `jira/writer.py` are both bounded, so they are unaffected — but a future
  query that forgets a restriction will fail here and pass elsewhere.
- **Atlassian API tokens begin `ATATT`**. A stray leading `-` from a paste caused a 401; that is the
  shape of the most likely user error.

### What is still blocked (two items, both needing the account owner)

1. **`ANTHROPIC_API_KEY` in `.env` is still a placeholder** — re-verified at export time. `generate`
   therefore cannot produce a draft, which blocks quickstart Scenarios 1, 2, 3, 4, 5, 6 and 10.
   The three Jira variables *are* set to real-looking values.
2. **No scratch project has been nominated.** The three visible projects all look like real work
   (`OHRM` = OrangeHRM automation with live stories; `SCRUM` and `TC` hold example content), and
   Scenarios 2 and 5 would file up to 25 real issues. **Nothing has been created in any project.**
   `JIRA_TARGET_PROJECT` must point at a throwaway project before any write scenario runs.

**Recorded order to work through once unblocked** (from `docs/quickstart-verification.md`): Scenario 1
(then open the CSV in a real spreadsheet and confirm `test_id` still reads as `TC-…`, not a number or
date — "the one failure the whole CSV design exists to prevent, and the only way to see it is to look")
→ Scenario 8's remaining rows (exits 4, 6, 7, 8) → Scenario 2 → Scenario 5 (interrupt a publish with
Ctrl+C, re-run `approve`, count issues; then the harder variant — edit a published row's summary and
re-approve, which must not create a second issue) → Scenario 6 with a real spreadsheet → Scenario 10
end-to-end with `--json | jq`.

## 9. Known, deliberately-accepted gaps

- **The constitution is an unfilled template.** `.specify/memory/constitution.md` still contains
  `[PRINCIPLE_1_NAME]`, `[SECTION_2_CONTENT]`, `[GOVERNANCE_RULES]` placeholders. `plan.md` reports
  this as a **gap, not a pass**: "the gate did not run because there was nothing to run it against,"
  and **recommends running `/speckit-constitution` before `/speckit-implement`** — specifically because
  the privacy posture settled during clarification (requirement text leaves the machine with no consent
  gate) "is exactly the kind of decision a constitution normally constrains, and it is currently
  governed only by an assumption in the spec." **That recommendation was not acted on**; implementation
  proceeded without it. The plan does note four practices the design adopted anyway, which the
  constitution template's own commented examples suggest: library-first (logic importable, CLI a thin
  shell), text-in/text-out with both human and machine formats, tests before implementation, and
  observable/debuggable output with redaction.
- **The spreadsheet fixtures are reproductions, not real saves.** `tests/fixtures/README.md` states the
  four CSVs were authored to reproduce byte-for-byte what Excel / LibreOffice / Google Sheets produce,
  but "were not produced by running" those applications, because none was available. Flagged as "a
  weaker guarantee than `contracts/draft-csv.md` asks for," with instructions to replace them with real
  saves.
- **Five success criteria cannot be automated.** `docs/adoption-metrics.md` exists to hold human
  measurement of SC-001, SC-002, SC-003, SC-009, SC-011. It is **an explicitly blank record**: "Do not
  cite this file as evidence that any criterion is met." Minimum worth a conclusion is stated as "at
  least five requirements across at least two people," and "not enough data" is named a legitimate
  verdict. It also warns that a *low* edit rate is not automatically good — "a 25-case draft approved
  in ninety seconds was not reviewed."
- **The quickstart verification harness is not checked in** — it lives in a session scratchpad. The
  record notes it could become `scripts/verify_quickstart.py` "if you want it as a permanent" file,
  which "would make this record reproducible rather than narrative." The user has not said either way.
- **No consent prompt and no redaction of requirement text.** `README.md` leads with this: running
  `generate PROJ-123` sends that issue's description and acceptance criteria to Anthropic's API, with
  no prompt and no attempt to strip names, customer details, or credentials. Organizational approval is
  assumed (FR-029). Jira credentials are never sent to the AI service.

## 10. Explicit out-of-scope list (from `spec.md` Assumptions)

One requirement per run (no epic/sprint/filter bulk) · manual test cases only, no automated scripts ·
native Jira issues, not a test-management add-on · single user per run, no multi-user approval
workflow or audit trail beyond local run artifacts · English-language requirement content · network
required, offline out of scope · the tool does not create projects, issue types, link types, or grant
permissions · the tool provides no editing surface of its own (review happens in the user's editor or
spreadsheet).

## 11. The user's own authored skills (QA domain, outside this repo)

Found in the user's synced skills, authored by them — these show the wider workflow this project sits in:

- **`manual-test-case-generator`** — generates a manual QA test case sheet (CSV) from *source code* for
  a named feature/module/file. Its hard rules are informative about the user's standards: read-only on
  the codebase; no hallucinated behavior (every case must trace to logic actually read, inferences
  marked "Assumption"); no secrets in output (use `test_user@example.com`, `<REDACTED>`); ask before
  guessing scope; always include negative/edge/boundary cases; **never mark anything "Passed" or
  "Verified"** — leave `Actual Result`/`Status` blank for the human tester; output to
  `test-cases/<feature-name>-test-cases.csv`; flag gaps with `Priority: TBD` rather than filling them
  silently.
- **`confluence-test-case-publishing`** — publishes QA test cases from CSV/XLSX into Confluence
  (`https://data-dc.atlassian.net/wiki`, REST prefix `/wiki/rest/api`) as an organized OrangeHrm page
  hierarchy; preserves Jira traceability, prevents duplicate pages/cases, and **"Do not assume an API
  request succeeded"** — check status codes and parse the JSON before reporting success. Credentials
  come from the host environment (`CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN`); the skill stores none.
  It explicitly chains: generation skill first, then Jira retrieval skill, then this.
- **`playwright-e2e-testing`** — Playwright E2E guidance, Python. Principles: user-centric journeys,
  resilient selectors (`getByRole`/`getByText`/`getByLabel`/`getByTestId` over CSS/XPath), rely on
  auto-waiting and avoid `waitForTimeout`, full test isolation, tests-as-documentation.

**Note the strong overlap**: the same conventions recur across the user's own skills and this project —
CSV as the test-case interchange format, Jira traceability, mandatory negative/edge coverage, never
claim success without verifying, and never put secrets in output.

## 12. Sibling project directories (same parent, same user)

`C:\project\Calude-Project\` also contains: `businessPlan`, `jira-integration`, `learnskill`,
`playwright-automation`, `playwright-automation-python`. The `confluence-test-case-publishing` skill
references `C:\project\Calude-Project\learnskill\test-cases` as an example input directory and names a
`jira-integeration` [sic] skill as a prerequisite.

## 13. Authoritative files to read first in a new session

| File | Why |
|---|---|
| `specs/001-jira-test-case-generator/spec.md` | Requirements (FR-001…FR-031), success criteria (SC-001…SC-012), clarifications, assumptions, edge cases |
| `specs/001-jira-test-case-generator/research.md` | **"The why behind the awkward decisions"** — R1–R8, including the R8 amendment |
| `specs/001-jira-test-case-generator/plan.md` | Technical context, constitution-check gap, project structure, complexity tracking |
| `specs/001-jira-test-case-generator/tasks.md` | 87 tasks, dependencies, parallel opportunities, phase notes |
| `specs/001-jira-test-case-generator/contracts/` | `cli.md`, `draft-csv.md`, `jira-api.md`, `generation.md` — treated as breaking-change surfaces |
| `specs/001-jira-test-case-generator/data-model.md`, `quickstart.md` | Entity constraints; the 10 manual scenarios |
| `docs/quickstart-verification.md` | What has actually been run against real Jira, and what is blocked |
| `docs/adoption-metrics.md` | The blank human-measurement record for SC-001/002/003/009/011 |
| `README.md` | User-facing contract: commands, options, exit codes, the external-AI disclosure |
| `tests/fixtures/README.md` | Fixture provenance caveat |

## 14. Draft CSV column contract (for any tool that must read the draft)

Ten columns, in order: `test_id`, `approval`, `kind`, `summary`, `preconditions`, `steps`,
`expected_results`, `traces_to`, `jira_key`, `notes`.

- `approval` ∈ `pending | approved | rejected`; `kind` ∈ `positive | negative | edge`
- `steps` and `expected_results` are numbered lines **inside one cell**; `expected_results` must hold
  either one entry per step or exactly one overall
- A blank `test_id` on a hand-added row gets one assigned; **editing an existing `test_id` is
  indistinguishable from a new case** and will be filed as one
- `jira_key` is informational only — clearing, sorting, or inventing a value cannot cause a duplicate
  or a skipped publish (identity comes from `state.json`)
- `notes` is for the reviewer and is **never published to Jira**

## 15. Exit-code table (the scripted-caller contract)

`0` success, including "nothing to publish" and an empty `drafts` list · `1` unexpected internal error ·
`2` malformed issue key or invalid arguments (**also a malformed `.env`**) · `3` issue not found ·
`4` no permission to read the issue · `5` authentication failure · `6` requirement content empty or too
sparse · `7` missing `CREATE_ISSUES` or `LINK_ISSUES` on the target project · `8` generation service
unreachable, refused, or unusable output · `9` no pending draft, or ambiguous run selection ·
`10` draft failed validation · `11` publish partially or wholly failed, re-runnable · `12` retries
exhausted against Jira · `13` site configuration unresolved (unknown field, issue type, or link type).

## 16. Configuration surface

| Variable | Required | Purpose |
|---|---|---|
| `JIRA_BASE_URL` | yes | e.g. `https://acme.atlassian.net` |
| `JIRA_EMAIL` | yes | Account email for the API token |
| `JIRA_API_TOKEN` | yes | From id.atlassian.net → Security → API tokens |
| `ANTHROPIC_API_KEY` | yes, for `generate` only | `approve` and `drafts` do not need it |
| `JIRA_TARGET_PROJECT` | no | Defaults to the source issue's project |
| `JIRA_AC_FIELD` | no | Acceptance criteria field name; defaults to reading the description |
| `JIRA_ISSUE_TYPE` | no | Issue type for created test cases (default `Task`) |
| `JIRA_LINK_TYPE` | no | Link relationship (default `Relates`) |

---

# INFERRED CONTEXT

Deductions from observed patterns. **The user did not state any of these.** Treat as hypotheses.

## Working style

- **Terse, command-driven prompts.** Every recorded prompt for this directory is either a slash command
  or a single-token answer (`A`, `B`, `C`, `D`, `yes`). No prose instructions, no stylistic direction.
  Inference: the user expects the workflow itself to carry the process, and answers multiple-choice
  clarification questions rather than writing narrative requirements.
- **Phase discipline is deliberate.** They never jumped from `/speckit-specify` to `/speckit-implement`.
  When the `.env` gap appeared mid-implementation, they went **back** to `/speckit-clarify` and
  re-ran `/speckit-tasks` rather than asking for a quick patch. Inference: artifacts are expected to
  stay the source of truth, and changes flow spec → plan/research → tasks → code.
- **Explicit batch sizing.** Task ranges of ~13–15 tasks per `/speckit-implement` call. Inference: they
  want reviewable increments and a checkpoint between phases, not one long unattended run.
- **They re-run a single task by ID when needed** (`/speckit-implement T073`), so addressing one task
  in isolation is an accepted interaction.
- **Remote Control is part of their setup** (`/remote-control SDD`, issued several times). Inference:
  they drive or monitor these sessions from another device, so long unattended runs matter to them.
- **Configured model preference**: `~/.claude/settings.json` sets `"model": "claude-opus-5"` and
  `"theme": "dark"`. Inference: they default to the most capable model rather than a cheaper one —
  consistent with the R7 model argument.

## Documentation and honesty expectations

The project's documents consistently separate *verified* from *decided*, mark fixtures as
reproductions, label the metrics file "a blank record, not a result," and state blockers plainly. This
is the established tone of the artifacts — so it is the convention to continue. **Caveat worth being
explicit about: those documents were written by the assistant, not by the user, so this reflects an
adopted house style the user accepted, not a preference they articulated.**

## Domain and role

The combination of three self-authored QA skills (manual test case generation, Confluence publishing,
Playwright E2E), the OrangeHRM test project, and this feature's target user ("QA engineers, test leads,
and developers who already work in Jira daily") suggests the user works in **QA / test engineering**
and is building a connected toolchain: requirement → generated cases → review → Jira issues, with
Confluence publishing alongside. Not stated anywhere.

## Technical leanings

- **Minimal dependency surface** — stdlib `csv` over a CSV library, hand-written `.env` parser over
  `python-dotenv`, hand-written ADF builder over a third-party ADF library. Three independent instances
  of the same preference, each with a recorded rationale.
- **Strictness on by default** — `mypy strict` + `warn_unreachable`, `--strict-markers`, branch
  coverage, a nine-rule ruff selection. Inference: they want the tooling to catch things rather than
  relying on review.
- **Windows-first, cross-platform-aware** — Windows is the development environment and CSV/encoding
  concerns are treated as first-class rather than afterthoughts.

---

# UNCERTAIN INFORMATION

Open questions and things explicitly **not** known. Another AI should ask rather than assume.

1. **Will the constitution ever be filled in?** `plan.md` recommends `/speckit-constitution` before
   implementing; implementation happened anyway. Unknown whether this was a conscious choice to skip it
   or simply not acted on yet.
2. **Which project is the scratch project?** Required before any write scenario. The user has not
   nominated one, and the three visible projects all appear to hold real work.
3. **Will a real `ANTHROPIC_API_KEY` be supplied, and is `claude-opus-5-5` accepted as the default?**
   The model choice is an assistant recommendation with a recorded cost argument; `research.md` says the
   cheaper-model tradeoff "is the user's call to make explicitly." There is no record of the user
   confirming it. The key has never been set, so **the generation path has never executed for real** —
   including the structured-output schema, streaming, and in-code cap enforcement.
4. **Should the verification harness become `scripts/verify_quickstart.py`?** Offered in
   `docs/quickstart-verification.md`; no answer on record.
5. **Will the spreadsheet fixtures be replaced with real application saves?** The replacement procedure
   is written down; whether the user has Excel/LibreOffice/Sheets available is unknown.
6. **Why is this not a git repository?** No `.git` directory. Deliberate or not-yet-done is unknown —
   which also means **there is no commit history to mine for intent**, and the `.gitignore` protecting
   `.env` is currently load-bearing for a repo that does not exist yet.
7. **Is a second feature (`002-…`) planned?** Numbering is sequential and only `001` exists. The spec
   names several "separate feature" candidates (redaction, a local generation engine, an approval gate,
   bulk generation across an epic) but none is scheduled.
8. **Relationship between this project and the sibling directories** (`jira-integration`, `learnskill`,
   `playwright-automation-python`) is not documented anywhere in this repo. The skills imply a chain;
   the repo does not confirm it.
9. **Adoption decision.** `docs/adoption-metrics.md` ends with a decision point including "we are not
   going to adopt this, if that is what the numbers say." No data, no verdict.
10. **Jira site scope.** `data-dc.atlassian.net` is confirmed as the site used for verification and in
    the Confluence skill, but whether it is a personal sandbox or an organizational instance — and
    therefore what the real data-handling constraints are — is unknown. FR-029's "organizational
    approval is a precondition" has not been confirmed as actually obtained.
11. **No stated preference on communication style, response length, or code-comment density** beyond
    what the artifacts themselves demonstrate. Nothing on record about preferred languages beyond
    Python here, or about CI, deployment, or packaging/distribution intent for `jira-testgen`.

---

## Deliberately excluded

Credential **values** (never read or reproduced here), and the user's email address and other personal
details not needed to continue this project. Variable *names* and resolution order are included because
they are part of the project's documented contract.
