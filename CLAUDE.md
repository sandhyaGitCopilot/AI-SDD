# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`jira-testgen` — a Python CLI that reads a Jira requirement, generates manual test cases from it with
the Claude API, pauses for human review in a CSV draft, then files the approved cases back into Jira
as linked issues. Three commands: `generate`, `approve`, `drafts`.

Not a git repository. Windows is the primary development environment, which is why CSV encoding,
`\r\n` line endings, and Excel round-tripping are treated as correctness concerns rather than polish.

## Commands

```bash
# Install (Python 3.11+)
python -m venv .venv
.venv/Scripts/activate              # Windows; source .venv/bin/activate elsewhere
pip install -e ".[dev]"

# The full check set
pytest
pytest --cov=jira_testgen --cov-report=term-missing
ruff check . && ruff format --check .
mypy src/jira_testgen                # strict, src only

# One file / class / test
pytest tests/unit/test_validate.py
pytest tests/unit/test_validate.py::TestDuplicateIdentifiers
pytest tests/unit/test_validate.py::TestDuplicateIdentifiers::test_duplicate_blocks_approval
pytest -m contract                   # markers: contract, integration, unit
pytest -k "roundtrip or redaction"
```

`pytest` must pass **with no credentials set**, and also with a `.env` present holding fake ones. The
autouse `isolate_environment` fixture in `tests/conftest.py` strips all eight `JIRA_*`/`ANTHROPIC_*`
vars *and* monkeypatches `config.default_dotenv_path` to `None`, so a developer's real `.env` cannot
leak into the suite. If a test needs credentials to pass, something is reaching the network that
should not be.

## Spec-driven development — read this before changing behavior

This repo is built with **Spec Kit** (`.specify/`, `speckit_version` 1.0.13, Python helper scripts).
The design documents are the source of truth, not the code, and the user drives work through slash
commands in phase order: `/speckit-specify` → `/speckit-clarify` → `/speckit-plan` → `/speckit-tasks`
→ `/speckit-implement <task range>`.

Behavior changes flow **spec → research/plan → tasks → code**. When a design decision is challenged,
the established practice is to amend the decision record in place with the reasons, leave the
superseded reasoning visible, and re-run `/speckit-tasks` — see the R8 amendment in `research.md`,
where `.env` support reversed a documented rejection and produced a whole new Phase 7 (T075–T088).
Do not silently change code that contradicts a spec document.

Almost every function carries an `FR-0xx` / `SC-0xx` / `R<n>` / `T<nnn>` citation in its docstring or
a comment. Keep that convention — it is how the code stays traceable to `spec.md`.

| Document | Holds |
|---|---|
| `specs/001-jira-test-case-generator/spec.md` | FR-001…FR-031, SC-001…SC-012, clarifications, assumptions, edge cases |
| `…/research.md` | **The why behind the awkward decisions** (R1–R8), each with rejected alternatives |
| `…/plan.md` | Technical context, project structure, complexity tracking |
| `…/tasks.md` | 87 tasks (all complete), dependencies, phase notes |
| `…/contracts/` | `cli.md`, `draft-csv.md`, `jira-api.md`, `generation.md` — **breaking-change surfaces** |
| `docs/quickstart-verification.md` | What has actually run against real Jira; what is blocked |

The constitution (`.specify/memory/constitution.md`) is still the **unfilled template** — every
principle is a placeholder. `plan.md` reports this as a gap, not a pass, and recommends
`/speckit-constitution`. There are no ratified principles to check a design against; do not invent
them.

## Architecture

A thin Typer shell over an importable package. `cli.py` only parses arguments and delegates, so every
layer is testable without a terminal.

```
cli.py                  → commands/{generate,approve}.py      (orchestration; step order IS the contract)
  jira/     client, reader, writer, permissions, linktypes, adf   (Jira REST + ADF both directions)
  generation/  engine, prompts, coverage                           (Claude API, structured outputs)
  draft/    csv_io, validate, state                                (the user-editable file + durable state)
  review.py  models.py  config.py  errors.py
```

`jira/`, `generation/`, and `draft/` are separate packages because each is the seam where a different
external contract lands — Jira's REST shapes, the model's output schema, the user's spreadsheet.
Keeping them apart is what lets the suite exercise CSV round-tripping and publish-retry logic without
touching the network.

### Four invariants that span multiple files

**1. `state.json` is the authority, never the CSV.** `draft/state.py` holds the publication record
keyed by `test_id`, written atomically (temp file + `os.replace`) **the instant each create
succeeds** — before the link, before the next case. The CSV's `jira_key` column is written back for
the user's information only and is *never read back* to make a decision. This is the single most
important rejection in the design: FR-014 invites the user to edit the draft freely and spreadsheets
rewrite files on save, so the one record that must survive cannot live there. Identity comes from
`test_id` (`TC-<4 hex>-<nnn>`), not summary text or row position — rewording a published case must
not create a second issue.

**2. Step order in `commands/generate.py` is ordered by cost.** Everything that can fail cheaply
fails first: local key validation (no network) → `verify_authentication()` → site config resolution →
fetch issue → report existing links → permission gate → *then* the model call. A user is never told
about a bad link type or a missing permission after money has been spent.

`verify_authentication()` exists for a non-obvious reason: **Atlassian Cloud answers most read
endpoints anonymously when a token is wrong** — `200` with an empty result, not `401`. Without a
`GET /myself` probe first, a bad token surfaced as "this site has no link type named 'Relates'"
(exit 13 instead of 5). Regression tests:
`tests/integration/test_generate_failures.py::TestAnonymousResponsesAreTreatedAsAuthFailures`.

**3. Read/write asymmetry in the retry policy.** `jira/client.py` honours `Retry-After`, else backs
off with jitter (`1s × 2^attempt`), 5 attempts max. **Writes are never retried on a bare 5xx or a
connection error** — that request may have succeeded with a lost response, and a blind retry would
create the duplicate SC-007 forbids. The client raises `AmbiguousWrite` instead, and
`jira/writer.py` reconciles against Jira with a bounded JQL query before deciding anything. Writes
are sequential by design; parallelising 25 creates was rejected.

`jira/writer.py` also repairs orphaned links *before* creating anything new, because an issue
recorded with `linked=False` is the more urgent problem.

**4. Credentials cannot reach output.** `config.py` owns resolution (CLI option → env var → `.env`
→ default; **the environment always wins**, so a stale file never overrides a fresh export) and
exposes secrets through a `Secret` type whose `__repr__`/`__str__` return `***`. A `RedactionFilter`
keyed on the *live values* is installed on the root logger, so an HTTP debug log, an exception repr
carrying auth headers, and a traceback are all covered — not just the path someone remembered. The
tool is strictly read-only on `.env`: never creates, appends, truncates, or logs it, and a malformed
line reports the file and 1-based line number **without quoting the value**, because the value is the
secret.

### Injection seams (why the tests can be honest)

- `generate` takes `generation_client` — tests pass `StubGenerationClient` (`tests/integration/conftest.py`).
- `approve` takes `publisher` behind a `Publisher` Protocol, with `jira/writer.py` imported lazily.
  That seam is what lets User Story 2 be tested end to end with **zero** Jira writes, which is the
  only honest way to assert the FR-013 review gate.
- Integration tests call `run_generate(**options)` / `run_approve(...)` directly and assert on
  `typer.Exit.exit_code`, rather than going through a CLI runner. Jira is mocked with `respx` at the
  transport layer; `jira_mock` registers every route the flow touches so a test overrides only what
  it cares about.

### Hard-won details that look like arbitrary choices

- **Draft CSV**: `utf-8-sig` (BOM) so Excel doesn't mangle non-ASCII, `QUOTE_MINIMAL`, `\r\n`, and a
  non-numeric `test_id` prefix so no spreadsheet coerces it to a number or date. The reader tolerates
  BOM-or-not and sniffs `,` vs `;` (LibreOffice in European locales). Ten columns, fixed order, in
  `draft/csv_io.COLUMNS`.
- **ADF both directions** (`jira/adf.py`): Jira Cloud v3 returns `400` for a plain-string
  `description`, so an outbound builder is mandatory, and descriptions come back as trees so an
  inbound extractor is too. Unsupported node types (`mediaSingle`, `inlineCard`, …) are collected into
  an unread list and **reported in the draft**, never silently dropped (FR-009).
- **Site-specific names are resolved at runtime, never hardcoded** (`jira/linktypes.py`): AC field,
  link type, issue type. Errors name both what was searched for and what the site actually has.
- **The 25-case cap is enforced in code after parsing**, not only in the prompt — "a prompt
  instruction is a request, not a constraint". `--max-cases 99` is rejected, not clamped.
- **Exit codes 0–13 are a contract** (`errors.py` ↔ `contracts/cli.md`, one class per code). Adding
  one is a breaking change: Phase 7 deliberately reused exit `2` for a malformed `.env` rather than
  minting a new code. `3`/`4`/`5` must stay distinguishable. This is also why `ruff`'s `N818` is
  ignored — exceptions are named for the condition they report (`IssueNotFound`, `AuthFailure`) to
  keep that one-to-one mapping legible.
- **Dependencies stay minimal on purpose**: stdlib `csv` over a CSV library, a hand-written `.env`
  parser over `python-dotenv`, a hand-written ADF builder over a third-party one. Each rejection has
  a recorded rationale; don't add a dependency to replace one of them without revisiting it.
- `ruff` excludes `.specify/` — Spec Kit's vendored scripts are not ours to restyle.

## Generation settings

Model `claude-opus-5-5` with adaptive thinking, `effort: "high"`, streaming, and structured outputs
carrying a JSON schema. Research R7 records why: Opus defaults to `medium` effort (one level below
the rest of the family), test-case quality is the product (SC-003, SC-005), and downgrading to a
cheaper model is "the user's call to make explicitly, not a default to assume". Forced tool use to
coerce JSON returns `400` on this model — structured outputs are the supported mechanism. Parsed
output is re-validated against the same Pydantic models, so a schema-valid but empty case (zero
steps) still fails as FR-031 unusable output.

## Current state

87 of 88 tasks are `[X]`; **T073 is `[~]` (partial)**. 753 tests pass; `ruff` and `mypy` clean. The
**read-only** half has been verified against a live Jira site (`data-dc.atlassian.net`) — all 9
Scenario 8 error paths, the credential-leakage sweep, and real ADF ingestion. **No write path has
ever run against real Jira.**

`## Phase 8: Convergence` (T089–T093) was appended by `/speckit-converge` on 2026-10-08 and holds the
remaining work: real spreadsheet fixtures, the live write scenarios, FR-005 in `--json`, the `.env`
shadowing notice, and recording the `commands/` structural deviation.

Two blockers, both needing the account owner:

1. `ANTHROPIC_API_KEY` in `.env` is still the placeholder, so `generate` cannot produce a draft.
2. No scratch project has been nominated. The visible projects (`OHRM`, `SCRUM`, `TC`) all hold real
   work, and the write scenarios would file up to 25 real issues. Nothing has been created anywhere.

Also open, and documented as such rather than quietly assumed away: the spreadsheet fixtures in
`tests/fixtures/` are faithful byte-level reproductions, **not** real application saves (see that
directory's README); `docs/adoption-metrics.md` is an explicitly blank record for the five success
criteria no command can assert; and the quickstart verification harness was never checked in.

Two site behaviors worth knowing: that site **rejects unbounded JQL** (the reconciliation queries in
`writer.py` are both bounded — a future query that forgets a restriction will pass elsewhere and fail
there), and Atlassian tokens begin `ATATT` (a stray pasted character is the likeliest 401).

## Out of scope (from `spec.md`)

One issue per run, no bulk across an epic or filter · manual test cases only, no automated scripts ·
native Jira issues, not a test-management add-on · single user, no multi-user approval or audit trail
beyond local run artifacts · English content · network required · the tool never creates projects,
issue types, link types, or grants permissions · no editing surface of its own — review happens in
the user's editor or spreadsheet.

Requirement text **is** sent to the Claude API with no consent prompt and no redaction; FR-029 makes
organizational approval a precondition of use rather than something the tool verifies. Adding
redaction, a local engine, or a policy gate would be a separate feature.
