# Tasks: Jira Test Case Generator

**Input**: Design documents from `/specs/001-jira-test-case-generator/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Included. The plan's project structure names the test tree, research R3/R6/R8 each specify behavior that "is asserted in tests", and [quickstart.md](./quickstart.md) opens with the suite. Tests are therefore part of this design, not an optional add-on.

**Organization**: Tasks are grouped by user story so each can be implemented and tested independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story the task serves (US1, US2, US3)
- Every task names an exact file path

## Path Conventions

Single project per [plan.md](./plan.md): `src/jira_testgen/` and `tests/` at repository root.

**One deviation from plan.md, made for story independence**: the plan put the permission precheck and issue creation together in `jira/writer.py`. They are split here into `jira/permissions.py` (US1, runs before generation) and `jira/writer.py` (US3, creates and links), so US1 and US3 never edit the same file.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project skeleton and tooling

- [X] T001 Create the package and test tree per plan.md: `src/jira_testgen/{jira,generation,draft}/__init__.py`, `src/jira_testgen/__init__.py`, `src/jira_testgen/__main__.py`, and `tests/{contract,integration,unit}/__init__.py`
- [X] T002 Create `pyproject.toml` requiring Python >=3.11 with runtime deps `typer`, `httpx`, `pydantic>=2`, `anthropic`, `rich`; dev deps `pytest`, `pytest-cov`, `respx`, `ruff`, `mypy`; and console entry point `jira-testgen = "jira_testgen.cli:app"`
- [X] T003 [P] Configure `ruff` (lint + format) and `mypy` (strict on `src/jira_testgen/`) in `pyproject.toml`
- [X] T004 [P] Configure pytest in `pyproject.toml`: `testpaths = ["tests"]`, coverage over `jira_testgen`, and a marker registry; add `tests/conftest.py` with a fixture that clears `JIRA_*` and `ANTHROPIC_API_KEY` from the environment so no test can accidentally reach a real service
- [X] T005 [P] Create `.gitignore` covering `.venv/`, `__pycache__/`, `.pytest_cache/`, `.coverage`, and the run workspace `.jira-testgen/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared infrastructure every user story depends on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T006 [P] Implement the typed error hierarchy in `src/jira_testgen/errors.py`, one class per exit code in contracts/cli.md: `InvalidArguments`(2), `IssueNotFound`(3), `IssueForbidden`(4), `AuthFailure`(5), `InsufficientContent`(6), `MissingProjectPermission`(7), `GenerationFailure`(8), `NoPendingDraft`(9), `DraftValidationFailure`(10), `PublishFailure`(11), `RetriesExhausted`(12), `SiteConfigUnresolved`(13). Each carries an `exit_code` and a remediation message (SC-008)
- [X] T007 [P] Implement `src/jira_testgen/config.py`: resolve `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `ANTHROPIC_API_KEY` from the environment plus non-secret settings from an optional config file; expose secrets through a `Secret` type whose `__repr__` and `__str__` return `***` (FR-027, research R8)
- [X] T008 Implement a logging redaction filter in `src/jira_testgen/config.py` keyed on the live secret values, installed on the root logger so tracebacks and HTTP debug logs cannot leak a token (FR-027)
- [X] T009 [P] Implement `SourceRequirement`, `AcceptanceCriterion`, and `LinkedIssueRef` in `src/jira_testgen/models.py` per data-model.md: `issue_key` matching `^[A-Z][A-Z0-9_]+-[0-9]+$`; validator rejecting the case where `description_text` and `acceptance_criteria_text` are both empty or whitespace (FR-004); `criterion_id` formatted `AC-<n>`
- [X] T010 Implement `TestCase` in `src/jira_testgen/models.py` per data-model.md with every constraint enforced: `test_id` matching `TC-<run-seq>-<nnn>`; `summary` 1–255 characters; `steps` at least 1, each non-empty after stripping; `expected_results` length exactly `1` or exactly `len(steps)`; `traces_to` at least 1 entry; `case_kind` one of `positive|negative|edge`; `approval` one of `pending|approved|rejected` defaulting to `pending`; `origin` one of `generated|user_added`
- [X] T011 Implement `Draft`, `RunState`, and `PublicationEntry` in `src/jira_testgen/models.py` per data-model.md: `Draft.test_cases` 1–25 items (FR-011); `RunState.schema_version` starting at `1`; `RunState.phase` one of `drafted|approved|publishing|published|rejected|failed`; `PublicationEntry` with `issue_key`, `issue_url`, and `linked` tracked separately
- [X] T012 [P] Write unit tests for every model constraint in `tests/unit/test_models.py`, including the expected-results-count rule (`1` or `len(steps)`, nothing else) and the 25-case cap
- [X] T013 Implement the Jira HTTP client in `src/jira_testgen/jira/client.py`: one `httpx.Client` per run, HTTP Basic auth, explicit connect and read timeouts, and the retry policy from contracts/jira-api.md — honour `Retry-After` on 429 and on 5xx carrying that header; jittered exponential backoff (`1s × 2^attempt`) for reads without it; **no retry for writes** on 5xx-without-header or connection errors; 5 attempts maximum, then raise `RetriesExhausted`
- [X] T014 [P] Write unit tests for the retry policy in `tests/unit/test_retry.py` using `respx`: `Retry-After` honoured; jittered backoff path; the read/write asymmetry (a write must not be retried on a bare 5xx — this is what protects SC-007); exhaustion raising `RetriesExhausted`
- [X] T015 Implement ADF handling in `src/jira_testgen/jira/adf.py`: `adf_to_text()` walking `paragraph`, `text`, `heading`, `bulletList`, `orderedList`, `listItem`, `codeBlock`, `blockquote`, `table`, `hardBreak`, `rule`, and collecting every other node type into an unread-types list (FR-009); `test_case_to_adf()` building the `{"type": "doc", "version": 1, "content": [...]}` body with Preconditions, Steps, and Expected Results sections (research R1)
- [X] T016 [P] Write unit tests in `tests/unit/test_adf.py` covering each supported node type, nested lists, a table, and an `mediaSingle` node being reported as unread rather than dropped
- [X] T017 Implement run state persistence in `src/jira_testgen/draft/state.py`: create/load/update `state.json`, writing atomically via a temp file in the same directory plus `os.replace` so an interrupted write cannot truncate the record; expose `record_issue_created(test_id, issue_key, url)` and `record_linked(test_id)` as separate operations (research R4)
- [X] T018 [P] Write unit tests in `tests/unit/test_state.py`: atomic replacement leaves no truncated file; a record written per create survives a simulated crash; `issue_key` set with `linked=False` is a recoverable state
- [X] T019 [P] Write unit tests in `tests/unit/test_redaction.py` asserting that a forced exception carrying an `Authorization` header, and a debug-level HTTP log, both produce output containing no secret substring (FR-027)
- [X] T020 Create the Typer application skeleton in `src/jira_testgen/cli.py` with the three commands declared (`generate`, `approve`, `drafts`), `--json` and `--workspace` common options, and a top-level handler mapping each `errors.py` class to its exit code, writing human output to stdout and diagnostics to stderr

**Checkpoint**: Shared models, HTTP, ADF, state, and CLI scaffolding ready — user stories can begin

---

## Phase 3: User Story 1 - Draft test cases from a Jira requirement (Priority: P1) 🎯 MVP

**Goal**: `jira-testgen generate PROJ-123 --no-wait` reads a Jira requirement, generates structured test cases covering negative and edge scenarios, and writes a reviewable CSV draft — touching nothing in Jira.

**Independent Test**: Run against a Jira issue that has a description and acceptance criteria; confirm a draft CSV exists with well-formed cases traced to the source criteria, and that no issues or links were created. Needs read access only.

### Tests for User Story 1

> **Write these first and confirm they fail before implementing**

- [X] T021 [P] [US1] Contract test for the generation output schema in `tests/contract/test_generation_schema.py`: valid payload parses; a case with zero steps is rejected; a case whose `expected_results` count is neither `1` nor `len(steps)` is rejected; a payload of 26 cases is rejected (contracts/generation.md G1–G6)
- [X] T022 [P] [US1] Contract test for the draft CSV writer in `tests/contract/test_draft_csv_contract.py`: exact header spelling and column order from contracts/draft-csv.md; `utf-8-sig` encoding; `\r\n` terminators; preamble lines present and prefixed `#`; a cell containing a comma, a quote, and a newline round-trips unchanged
- [X] T023 [P] [US1] Integration test for the generate flow in `tests/integration/test_generate_flow.py` with `respx`-mocked Jira and a stubbed generation client: asserts a draft is written, and asserts **zero** write calls were made to Jira (FR-013, SC-006)
- [X] T024 [P] [US1] Integration test in `tests/integration/test_generate_failures.py` for each early-exit path: malformed key exits `2` with no network call; 404 exits `3`; 403 exits `4`; 401 exits `5`; empty description and no criteria exits `6` **and writes no draft**; generation failure exits `8` **and writes no partial draft**

### Implementation for User Story 1

- [X] T025 [P] [US1] Implement site configuration discovery in `src/jira_testgen/jira/linktypes.py`: resolve the link type via `GET /rest/api/3/issueLinkType` matching `name`/`inward`/`outward`, and validate the test case issue type against `GET /rest/api/3/issue/createmeta?projectKeys={target}`; raise `SiteConfigUnresolved` (exit 13) listing what the site does have (research R5)
- [X] T026 [US1] Implement issue reading in `src/jira_testgen/jira/reader.py`: discover the acceptance criteria field via `GET /rest/api/3/field` matched case-insensitively by name, fetch the issue with `fields=summary,description,issuelinks,issuetype,project,{acFieldId}`, convert the description through `adf_to_text()`, and populate `fields_read` and `unread_node_types` (FR-002, FR-009)
- [X] T027 [US1] Implement acceptance criteria extraction in `src/jira_testgen/jira/reader.py`: split the criteria field (or description fallback) into `AcceptanceCriterion` records with `AC-<n>` ids, setting `machine_readable=False` when criteria are prose rather than a list (drives the SC-004 shortfall disclosure)
- [X] T028 [US1] Implement the existing-linked-tests report in `src/jira_testgen/jira/reader.py`, building `LinkedIssueRef` records from the fetched `issuelinks` so they can be shown before generating (FR-005)
- [X] T029 [P] [US1] Implement the permission precheck in `src/jira_testgen/jira/permissions.py` calling `GET /rest/api/3/mypermissions?projectKey={target}&permissions=CREATE_ISSUES,LINK_ISSUES` — the `permissions` parameter is mandatory — and raising `MissingProjectPermission` (exit 7) naming the project and the specific missing permission. Treat `LINK_ISSUES: false` as a hard stop, not a degraded mode (FR-022, research R2)
- [X] T030 [P] [US1] Write the generation prompt in `src/jira_testgen/generation/prompts.py`: tester role, required structure, explicit demand for negative and edge coverage, the self-contained-case rule (FR-010), the case cap, and the enumerated criteria with their ids so `traces_to` can reference them
- [X] T031 [US1] Implement the generation engine in `src/jira_testgen/generation/engine.py` using the `anthropic` SDK: model `claude-opus-5-5`, `thinking={"type": "adaptive"}`, `output_config={"effort": "high"}` set **explicitly** because this model defaults to `medium`, streaming, and structured outputs via `output_config.format` carrying the schema in contracts/generation.md. Accept an injected client so tests never call the real API
- [X] T032 [US1] Implement post-parse validation G1–G6 in `src/jira_testgen/generation/engine.py`: at least one case; per-case expected-results count of `1` or `len(steps)`; no whitespace-only steps; case count enforced **in code** against `--max-cases` rather than trusted to the prompt; unknown `traces_to` ids downgraded to a warning; every case re-validated against the `TestCase` model. Any failure raises `GenerationFailure` (exit 8) naming the check
- [X] T033 [US1] Implement generation failure mapping in `src/jira_testgen/generation/engine.py` for unreachable, `401`/`403`, `429` after retries, over-limit request, and `stop_reason == "refusal"` — each a distinct message that names the generation service rather than Jira (FR-031). Never silently truncate input content
- [X] T034 [US1] Assign stable identifiers in `src/jira_testgen/generation/engine.py` after parsing, formatted `TC-<run-seq>-<nnn>` and always starting with letters so no spreadsheet coerces them to a number or date (FR-006a, research R6). Identifiers are assigned locally, never taken from model output
- [X] T035 [P] [US1] Implement coverage analysis in `src/jira_testgen/generation/coverage.py`: criteria with no covering case, the negative/edge proportion for SC-005, a cap-reached note naming what was left uncovered, and unread ADF node types carried from ingestion — all emitted as `coverage_notes` (FR-009, SC-004)
- [X] T036 [US1] Implement the CSV writer in `src/jira_testgen/draft/csv_io.py` per contracts/draft-csv.md: `utf-8-sig`, comma delimiter, `QUOTE_MINIMAL`, `\r\n` terminators, the 10 columns in exact order, the `#` preamble carrying run id, source issue, fields read, generating model, timestamp, unread content, coverage notes, and the do-not-edit warning for `test_id`
- [X] T037 [US1] Implement the CSV reader in `src/jira_testgen/draft/csv_io.py`: accept UTF-8 with or without BOM, sniff comma vs semicolon delimiter, skip `#` preamble lines, normalise `\r\n`/`\r`/`\n`, and parse multi-line `steps`/`expected_results` cells by splitting on newlines and stripping an optional leading `N.`, `N)`, or `-` (research R6)
- [X] T038 [P] [US1] Write unit tests in `tests/unit/test_csv_io.py` for the tolerant reader: BOM and no-BOM, semicolon delimiter, inconsistent hand-renumbering, and a cell containing both a comma and an embedded newline
- [X] T039 [US1] Wire the `generate` command in `src/jira_testgen/cli.py` in the order given by contracts/cli.md: validate key shape locally → resolve site config → fetch issue → check content sufficiency → report existing linked tests → permission precheck → generate → write draft and state → exit. Add `--target-project`, `--ac-field`, `--issue-type`, `--link-type`, `--max-cases` (reject values above 25 rather than clamping silently), and `--no-wait`

**Checkpoint**: US1 complete. A QA engineer can produce a reviewable draft from a Jira issue with no write access and nothing created in Jira — the MVP described in spec US1.

---

## Phase 4: User Story 2 - Review, edit, and approve before anything reaches Jira (Priority: P1)

**Goal**: The tool halts after writing the draft, the engineer edits it freely in a spreadsheet or text editor, and nothing reaches Jira until they explicitly approve. An interrupted review resumes later without regenerating.

**Independent Test**: Testable from a **fixture draft** without running US1 — place a known `testcases.csv` and `state.json` in a run directory, then exercise validate, reject, subset approval, and resume. No generation and no Jira writes needed.

### Tests for User Story 2

- [X] T040 [P] [US2] Contract test in `tests/contract/test_cli_contract.py` asserting every command, option, and exit code in contracts/cli.md exists and matches — this is the file that makes the CLI contract breaking-change-detectable
- [X] T041 [P] [US2] Integration test in `tests/integration/test_draft_validation.py` for rules V1–V11: missing column, duplicate `test_id` reporting **both** row numbers, bad `approval` value, empty `summary`, zero steps, mismatched expected-results count, over-25 rows, unreadable CSV. Each must exit `10` and publish nothing
- [X] T042 [P] [US2] Integration test in `tests/integration/test_resume_after_interrupt.py`: abandon the review prompt, then approve the run in a fresh invocation and assert publishing proceeds with **no** Jira re-read and **no** generation call (FR-017, SC-010)
- [X] T043 [P] [US2] Integration test in `tests/integration/test_spreadsheet_roundtrip.py` using fixture CSVs **saved by a real spreadsheet application** (checked into `tests/fixtures/`), not files the tool wrote — asserting step order and identifiers survive re-encoding, delimiter changes, and re-quoting (SC-012)
- [X] T044 [P] [US2] Integration test in `tests/integration/test_reject_flow.py`: rejecting a draft exits `0`, creates nothing in Jira, and leaves the draft on disk for reuse (FR-015, SC-006)

### Implementation for User Story 2

- [X] T045 [US2] Implement draft validation V1–V11 in `src/jira_testgen/draft/validate.py` per contracts/draft-csv.md, reporting every finding by **row number as the user's editor numbers it** (counting preamble and header) and by column name. Fatal: V1–V7, V10, V11. Warning: V8 (unknown criterion id) and V9 (a published `test_id` missing from the file) (FR-016)
- [X] T046 [US2] Implement identifier handling for edited drafts in `src/jira_testgen/draft/validate.py`: a blank `test_id` is accepted as `origin=user_added` and assigned a fresh identifier; a duplicated identifier is a hard rejection naming both rows; a changed identifier is treated as new (FR-023a, FR-006a)
- [X] T047 [P] [US2] Write unit tests in `tests/unit/test_validate.py` for each rule in isolation, including the user-added blank-identifier path and the both-row-numbers duplicate report
- [X] T048 [US2] Implement the interactive review prompt in `src/jira_testgen/review.py` using `rich`: show the draft path, case count, and negative/edge proportion, then wait for approve / reject / open-editor. This is the FR-013 gate — it must be impossible to reach a Jira write without passing through it
- [X] T049 [US2] Implement subset approval in `src/jira_testgen/review.py` honouring both the per-row `approval` column and the `--only` option, and the nothing-approved case exiting `0` with "nothing to publish" as a **success**, not a failure (FR-015, FR-018)
- [X] T050 [US2] Implement run discovery in `src/jira_testgen/draft/state.py`: list runs under the workspace with phase, age, case count, and published count; resolve a bare `approve` to the most recent pending run; raise `NoPendingDraft` (exit 9) listing candidates when the choice is ambiguous rather than guessing (FR-017a)
- [X] T051 [US2] Wire the `drafts` command in `src/jira_testgen/cli.py` to render that listing as a `rich` table, with `--json`, exiting `0` even when the list is empty
- [X] T052 [US2] Wire the `approve` command in `src/jira_testgen/cli.py` per contracts/cli.md: resolve run → re-read the CSV from disk so the user's edits are the input → validate → `--reject`, `--only`, and `--dry-run` paths. `--dry-run` must report what would be created while touching nothing in Jira
- [X] T053 [US2] Make the blocking review prompt the default for `generate` in `src/jira_testgen/cli.py` (suppressed by `--no-wait`), and implement `--yes` to approve without review with help text stating plainly that it bypasses the FR-013 safety gate. `--yes` still runs validation

**Checkpoint**: US1 and US2 both work independently. The safety gate is real: no path reaches Jira without explicit approval.

---

## Phase 5: User Story 3 - Publish approved test cases as linked Jira issues (Priority: P2)

**Goal**: Approved test cases become Jira issues linked to the source requirement, with no duplicates ever — including after an interrupted publish is re-run.

**Independent Test**: Approve a fixture draft against a mocked (or scratch) Jira project and confirm each approved case exists as an issue with the expected body and is linked to the source requirement.

### Tests for User Story 3

- [X] T054 [P] [US3] Integration test in `tests/integration/test_approve_publish_flow.py`: N approved cases produce N issues, each linked to the source; the result report lists every issue key with a URL (FR-024); rejected and pending cases are not published
- [X] T055 [P] [US3] Integration test in `tests/integration/test_partial_publish_retry.py` — the SC-007 test: interrupt publishing after some issues are created, re-run approve, and assert only the outstanding cases are created and the total equals the approved count with no duplicate summaries
- [X] T056 [P] [US3] Integration test in `tests/integration/test_republish_guards.py`: re-approving a fully published draft reports "already published" and creates nothing; **editing a published case's summary and re-approving does not create a second issue**, proving identity comes from `test_id` and not summary text (research R4)
- [X] T057 [P] [US3] Integration test in `tests/integration/test_publish_failures.py`: `400` on a rejected field stops with the field named and is not retried; `403` mid-run exits `7`; retries exhausted exits `12` leaving the run resumable; a dropped connection after send is **not** blindly retried

### Implementation for User Story 3

- [X] T058 [US3] Implement issue creation in `src/jira_testgen/jira/writer.py`: `POST /rest/api/3/issue` with `project.key`, `issuetype.id`, `summary`, and a `description` built by `test_case_to_adf()` — a plain string returns HTTP 400, so the ADF document is mandatory (research R1)
- [X] T059 [US3] Record each created issue key via `state.record_issue_created()` **immediately on `201`, before the link is attempted and before moving to the next case**, in `src/jira_testgen/jira/writer.py`. Batching this at the end would widen crash damage from one ambiguous case to all 25 (research R4)
- [X] T060 [US3] Implement linking in `src/jira_testgen/jira/writer.py`: `POST /rest/api/3/issueLink` with the resolved link type and the created issue plus the source requirement, recorded via `state.record_linked()` separately from creation so a crash between the two calls stays recoverable (FR-020)
- [X] T061 [US3] Implement the skip-and-repair pass in `src/jira_testgen/jira/writer.py`: skip any `test_id` already holding an issue key, and before creating anything new, repair entries that have an issue key but `linked == False` (FR-023, FR-020)
- [X] T062 [US3] Implement ambiguous-write reconciliation in `src/jira_testgen/jira/writer.py`: on a connection drop after send, do not retry — query `GET /rest/api/3/search/jql?jql=issue in linkedIssues("<KEY>")` and reconcile by summary before deciding, reporting the ambiguity to the user (research R4)
- [X] T063 [US3] Implement sequential publishing with per-case error capture in `src/jira_testgen/jira/writer.py`, writing `last_error` (redacted) into the publication entry. Writes stay sequential by design — parallelising 25 creates invites rate limiting and complicates partial-failure state for a saving measured in seconds
- [X] T064 [US3] Implement the end-of-run report in `src/jira_testgen/cli.py`: a `rich` table of every created issue key with its URL plus every failure and its reason, and the matching `--json` shape (FR-024)
- [X] T065 [US3] Write back the `jira_key` column into the draft CSV after publishing via `src/jira_testgen/draft/csv_io.py`, as **informational only** — the duplicate guard reads `state.json` and must never read this column (contracts/draft-csv.md)
- [X] T066 [US3] Set terminal run phases in `src/jira_testgen/draft/state.py` (`publishing` → `published` or `failed`) and map a partial or whole publish failure to exit `11`, re-runnable

**Checkpoint**: All three user stories independently functional. The full spec flow works end to end.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T067 [P] Write `README.md` at repository root with install, environment variables, the three commands, and a plain statement that requirement text is sent to an external AI service with no consent prompt (FR-029)
- [X] T068 [P] Audit every error message in `src/jira_testgen/errors.py` against SC-008: each must name what went wrong **and** what to do next. Walk the table in quickstart.md Scenario 8 and read the messages, not just the exit codes
- [X] T069 [P] Add `tests/unit/test_exit_codes.py` asserting each error class maps to the exact code in contracts/cli.md, so a renumbering cannot pass silently
- [X] T070 Verify `--json` output on all three commands emits one parseable object on stdout with no decorative output mixed in (FR-028, quickstart Scenario 10)
- [X] T071 [P] Add a structured per-run log file in the run directory via `src/jira_testgen/config.py`, with the redaction filter attached, so a failed run is diagnosable after the fact
- [X] T072 Run the full `pytest` suite with **no credentials set** and confirm it passes — anything that fails is reaching the network and must be mocked
- [~] T073 **PARTIAL — read-only half done against live Jira 2026-10-07; writes and generation still blocked. See [docs/quickstart-verification.md](../../docs/quickstart-verification.md)**. Executed against the real site: Scenario 8 (9/9 error paths), Scenario 9 (credential-leakage greps with a real and a deliberately invalid token), and the ingestion path (ADF conversion, criteria extraction, FR-009 unread-node reporting, permission check). Found and fixed one defect: a bad token reported exit 13 instead of 5, because Atlassian Cloud answers /issueLinkType anonymously with HTTP 200. **Blocked on**: ANTHROPIC_API_KEY is still the placeholder (blocks Scenarios 1-6, 10), and no scratch project has been nominated for the scenarios that create issues.
- [X] T074 Create `docs/adoption-metrics.md` to record the measurement-based criteria that no single command can assert — SC-001, SC-002 (timing), SC-003 (edit rate), SC-009 (tester comprehension), SC-011 (review time at 25 cases) — captured during first-week real use, per quickstart's coverage map

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks all user stories**
- **US1 (Phase 3)**: depends on Foundational
- **US2 (Phase 4)**: depends on Foundational. Independently testable from a fixture draft, so it does **not** require US1 to be finished
- **US3 (Phase 5)**: depends on Foundational. Independently testable from a fixture draft and a mocked Jira
- **Polish (Phase 6)**: depends on the stories you intend to ship

### Why the stories are genuinely independent

US2 and US3 both consume a draft, and a draft is produced by US1 — so on paper they look chained. They are decoupled by the fixture draft in `tests/fixtures/`: US2 and US3 read a committed `testcases.csv` plus `state.json` rather than generating one. Two developers can work Phase 4 and Phase 5 in parallel the moment Phase 2 lands.

### Within each story

- Tests first, confirmed failing, then implementation
- Models before the services that use them
- `csv_io.py` write and read (T036, T037) before validation (T045), which is why the CSV reader sits in US1 even though US2 is its heaviest consumer

### Known serialization points

- T010 and T011 both edit `models.py` and follow T009 — not parallel
- T026, T027, T028 all edit `jira/reader.py` — sequential
- T031 through T034 all edit `generation/engine.py` — sequential
- T036 and T037 both edit `draft/csv_io.py` — sequential
- T058 through T063 all edit `jira/writer.py` — sequential
- T039, T051, T052, T053, T064 all edit `cli.py` — sequential across stories

---

## Parallel Opportunities

**Phase 1**: T003, T004, T005 together after T001–T002.

**Phase 2**: T006, T007, T009 together; then T012, T014, T016, T018, T019 (all separate test files) together.

**Phase 3 (US1)** — launch all four test files together before implementing:

```bash
Task: "Contract test for generation output schema in tests/contract/test_generation_schema.py"
Task: "Contract test for draft CSV writer in tests/contract/test_draft_csv_contract.py"
Task: "Integration test for generate flow in tests/integration/test_generate_flow.py"
Task: "Integration test for generate failure paths in tests/integration/test_generate_failures.py"
```

Then T025, T029, T030, T035 together (four separate modules).

**Phase 4 (US2)**: T040–T044 together (five separate test files).

**Phase 5 (US3)**: T054–T057 together (four separate test files).

**Phase 6**: T067, T068, T069, T071 together.

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → 2. Phase 2 Foundational → 3. Phase 3 US1 → 4. **Stop and validate** with quickstart Scenario 1 → 5. Demo.

The MVP is genuinely useful on its own: a QA engineer gets a reviewable draft of test cases from a Jira issue in under two minutes, with read-only access and nothing written anywhere. That is the bulk of the manual effort removed before a single line of publishing code exists.

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. + US1 → **MVP**, validate with Scenario 1
3. + US2 → the safety gate and resume, validate with Scenarios 2, 3, 4, 6, 7
4. + US3 → publishing, validate with Scenarios 2, 5
5. + Polish → validate with Scenarios 8, 9, 10

### Parallel Team Strategy

After Phase 2: Developer A takes US1 (ingestion and generation), Developer B takes US2 (review, validation, resume) against the fixture draft, Developer C takes US3 (publishing) against mocked Jira. The fixture draft is the contract between them — agree its contents when Phase 2 closes, before the three split.

---

## Notes

- `[P]` means different files with no incomplete dependencies
- Commit after each task or logical group
- Three tasks carry disproportionate weight: **T013** (read/write retry asymmetry), **T017** (atomic state writes), and **T059** (recording the issue key before linking). Together they are what make SC-007's no-duplicates guarantee true; a shortcut in any one of them breaks it quietly, and the failure shows up as duplicated issues in a shared Jira project rather than as a failing test
- The constitution is still the unfilled template, so no governance gates constrain these tasks. Running `/speckit-constitution` before `/speckit-implement` is still recommended — the privacy posture in FR-029 is currently governed only by a spec assumption

---

## Phase 7: Credential loading from `.env` (FR-026a → FR-026c)

**Added**: 2026-10-07, from the `/speckit-clarify` session of the same date. Not a new user story — it is cross-cutting configuration that every command depends on, so tasks here carry no `[Story]` label.

**Goal**: `jira-testgen` reads credentials from a `.env` file in the working directory as well as from the environment, so a user does not re-export four variables in every new terminal.

**Independent Test**: With no credential variables exported and a `.env` holding all four, `jira-testgen drafts` and `jira-testgen approve --dry-run` work against a fixture run. Then export one variable with a different value and confirm the exported value wins.

**Why this phase exists**: research R8 originally rejected any file-based credential source, but the spec's own Assumptions permitted "a local configuration the user controls", so the implementation ended up narrower than the spec allowed. The gap surfaced only when a user tried to supply credentials. R8 now carries an amendment recording the reversal and the residual risk.

**Already done during clarification, not re-tasked here**: `.gitignore` gained a `!.env.example` negation, because the pre-existing `.env.*` rule would otherwise have excluded the very template FR-026b requires to ship.

### Tests for Phase 7

> **Write these first and confirm they fail before implementing.** T075 comes first for a reason — see its note.

- [X] T075 [P] Make the test suite immune to a developer's real `.env` in `tests/conftest.py`: extend the autouse `isolate_environment` fixture so no test can pick up credentials from a `.env` file on disk, by pointing credential resolution at an empty directory (or an explicit opt-out flag) rather than the repository root. **Write this before the loader exists.** Once `load_settings` reads `.env`, any developer with a real `.env` in the repo root would have live credentials injected into every test, which would both break the T072 guarantee that the suite passes with no credentials and let a test reach a real Jira site
- [X] T076 [P] Write unit tests for the `.env` parser in `tests/unit/test_dotenv.py`: `NAME=value`; surrounding single or double quotes stripped; whitespace around the name and the `=` tolerated; blank lines and lines beginning `#` skipped silently; an optional leading `export ` tolerated; a missing file returns empty rather than raising (FR-026a); a line that is not `NAME=value` raises `InvalidArguments` naming the file and the 1-based line number, with the line's **value absent from the message** (FR-026c)
- [X] T077 Write precedence tests in `tests/unit/test_dotenv.py`: a name already set in the environment keeps its environment value; a name absent from the environment takes the file value; a name in neither raises the existing "Required environment variable ... is not set" error unchanged (FR-026a)
- [X] T078 [P] Extend `tests/unit/test_redaction.py` to assert a secret loaded from `.env` is redacted identically to an exported one: it must not appear in the run log, in `state.json`, or in a forced traceback (FR-026b, FR-027)
- [X] T079 [P] Assert in a new `tests/unit/test_config.py` that the tool never creates, truncates, or appends to a `.env` file: run `load_settings` against a fixture `.env` and compare the file bytes before and after (FR-026b)

### Implementation for Phase 7

- [X] T080 Implement `load_dotenv_file(path: Path) -> dict[str, str]` in `src/jira_testgen/config.py`: parse `NAME=value` per T075's rules, read-only, no third-party dependency — the format is a few lines of parsing and adding `python-dotenv` for it would enlarge the dependency surface for no gain. Return an empty dict for a missing file
- [X] T081 Wire the loader into `load_settings` in `src/jira_testgen/config.py` so it runs before `_require_env`, filling only names absent from `os.environ` (FR-026a). The environment must win; a stale file must never override an export the user just made. Resolve the file as `.env` relative to the current working directory, and allow the path to be injected so tests do not depend on where pytest was invoked from
- [X] T082 Implement the malformed-file failure in `src/jira_testgen/config.py`: raise `InvalidArguments` (**exit 2 — do not add a new exit code**, since contracts/cli.md treats the code table as a breaking-change surface and a bad config file is an invalid-argument condition) naming the file and the 1-based line number, and never including the offending line's value (FR-026c)
- [X] T083 Correct the now-stale docstring in `src/jira_testgen/config.py` that reads "Secrets come only from the environment -- never from a config file, which would get committed (research R8)". It is accurate about the current code and becomes false the moment T081 lands; a reader following its R8 citation now finds the opposite conclusion
- [X] T084 [P] Create `.env.example` at the repository root holding all four credential names with **placeholder values only** and a comment stating that a real `.env` is gitignored and must never be committed (FR-026b)

### Documentation for Phase 7

- [X] T085 [P] Update the Configure section of `README.md` to document `.env` alongside the shell-export form, state that the environment wins on conflict, and say plainly that a token in `.env` is plaintext on disk — recording the tradeoff rather than presenting the file as strictly safer
- [X] T086 [P] Update the Setup section of `specs/001-jira-test-case-generator/quickstart.md` to offer `cp .env.example .env` as the alternative to exporting four variables, and extend Scenario 9 to grep `.env` for the token as well, so the leakage check covers the new file
- [X] T087 [P] Correct the Auth line in `specs/001-jira-test-case-generator/contracts/jira-api.md`, which reads "from the environment (research R8)", to say the environment or a `.env` file, so the contract does not contradict FR-026a

### Validation for Phase 7

- [X] T088 Re-run the T072 guarantee two ways from the repository root: once with no credentials set and **no** `.env` present (the suite must pass), and once with no credentials set and a `.env` holding fake credentials present (the suite must still pass and must still make no network call). The second run is what proves T075 actually works — without it, the suite's independence from real credentials is silently conditional on the developer not having a `.env`

### Phase 7 dependencies

- Depends on Phase 1 and Phase 2 only. It touches `config.py`, `conftest.py`, and documentation; it does not depend on any user story, and no user story depends on it (every command already works with exported variables)
- **T075 before T081.** Wiring the loader in before the test fixture is hardened would inject a developer's real credentials into the whole suite
- T080 → T081 → T082 → T083 all edit `config.py` — sequential
- T076 → T077 both edit `tests/unit/test_dotenv.py` — sequential
- T088 last: it validates the rest

### Phase 7 parallel opportunities

- T075, T076, T078, T079 together (four separate test files), then T077 after T076
- T084, T085, T086, T087 together (four separate files, no shared edits)

---

## Phase 8: Convergence

**Source**: `/speckit-converge` assessment, 2026-10-08, run after Phase 7 implementation. Append-only: no existing task was altered, renumbered, or reordered.

**Assessment baseline**: 753 tests pass, `ruff check` / `ruff format --check` clean, `mypy src/jira_testgen` clean under strict. No constitution checks ran -- `.specify/memory/constitution.md` is still the unmodified template, so there are no ratified principles to assess against.

- [ ] T089 Replace the four draft CSV fixtures in `tests/fixtures/` with files genuinely saved by Excel for Windows ("CSV UTF-8"), LibreOffice Calc in a semicolon locale, and Google Sheets, then re-run `tests/integration/test_spreadsheet_roundtrip.py` unchanged, per SC-012 and T043 (partial). T043 requires fixtures "saved by a real spreadsheet application ... not files the tool wrote", but `tests/fixtures/README.md` records that all four were hand-authored to reproduce those applications byte for byte because none was available in the generating environment. The round-trip test therefore passes against reproductions of the failure mode rather than the failure mode itself. Follow the replacement procedure in that README, and update its Provenance section once the files are real saves
- [ ] T090 Execute the live-site scenarios T073 left open -- quickstart Scenarios 1, 2, 3, 4, 5, 6 and 10, plus the remaining Scenario 8 rows (exits `4`, `6`, `7`, `8`) -- and record the results in `docs/quickstart-verification.md`, per T073 and US3 (partial). T073 is marked `[~]`: only the read-only half ran against the real site, so no code path that creates or links a Jira issue, and no generation call, has ever executed outside a mock. This is the only end-to-end evidence for FR-019 through FR-024, SC-007 and SC-010. **Blocked on two inputs only the account owner can supply**: a real `ANTHROPIC_API_KEY` (the value in `.env` is still the `.env.example` placeholder) and a nominated throwaway `JIRA_TARGET_PROJECT` (the visible `OHRM`, `SCRUM` and `TC` projects all hold real work, and Scenarios 2 and 5 would file up to 25 issues). Work through the order already recorded in `docs/quickstart-verification.md`
- [ ] T091 Include the test cases already linked to the source issue in the `generate` `--json` payload in `src/jira_testgen/commands/generate.py`, per FR-005 (partial). `_report_existing_links` is called only under `if not json_output`, and the payload has no equivalent key, so a scripted caller -- the one FR-028 exists for -- is never told that the requirement already has test cases linked from a previous run. The data is already on hand (`source.existing_linked_tests`, persisted in `state.json`); only the machine-readable surface omits it. `contracts/cli.md` does not pin the payload shape, so adding a key is not a contract break. Cover it in `tests/integration/test_json_output.py`
- [ ] T092 Report when a `.env` value was not used because the same name is set in the environment, in `src/jira_testgen/config.py`, per spec Edge Cases and FR-026a (partial). The precedence itself is correct -- `_resolve` prefers the live environment value -- but it does so silently, while the spec's edge case requires that "the environment value is used, and the tool is explicit that the file value was not, so a stale file cannot silently override an export the user just made". Being explicit is the half that makes the edge case diagnosable rather than merely safe. Emit the shadowed **names** only, never values (FR-026b, FR-027), and assert it in `tests/unit/test_dotenv.py`
- [ ] T093 Reconcile the `src/jira_testgen/commands/` package with the documented project structure, per plan: Project Structure (unrequested). `commands/generate.py` and `commands/approve.py` hold the orchestration that `plan.md` placed in `cli.py` and that T020 names there, and the package appears in neither `plan.md` nor `tasks.md` -- unlike the `jira/permissions.py` split, which `tasks.md` records explicitly under Path Conventions. The split is sound and keeps `cli.py` the thin shell the plan's Structure Decision calls for, so the remaining work is to record it as a second deviation in the Path Conventions section rather than to undo it. Surfaced for traceability only; no behavior change

### Phase 8 dependencies

- All five are independent of one another and touch separate files; none blocks another
- T089 and T090 need physical inputs (spreadsheet applications; a generation key and a scratch project) and are not completable by code changes alone
- T091, T092 and T093 are ordinary code and documentation work with no external dependency

### Phase 8 parallel opportunities

- T091, T092 and T093 together (three separate files, no shared edits)
- T089 and T090 overlap naturally: Scenario 6 is the spreadsheet round trip, so a session with the applications open can satisfy both

