# Implementation Plan: Jira Test Case Generator

**Branch**: `001-jira-test-case-generator` | **Date**: 2026-10-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-jira-test-case-generator/spec.md`

## Summary

A command-line tool that reads a Jira requirement, generates manual test cases for it with an external AI model, pauses for human review in a CSV draft, and then files the approved cases back into Jira as linked issues.

The technical approach is a single Python CLI package with three commands (`generate`, `approve`, `drafts`) over four internal layers: a Jira read/write client, a generation engine, a CSV draft reader/writer, and a run-state store. The run-state store is the load-bearing piece — it is what makes the review pause survivable (FR-017) and re-publishing duplicate-free (FR-023), and it is deliberately kept separate from the CSV because a spreadsheet application may rewrite the CSV on save.

Three decisions shape the design and are justified in [research.md](./research.md): Jira body fields are Atlassian Document Format trees rather than text, so an ADF builder and a reverse text extractor are needed on both edges; permissions and link types are discovered at runtime rather than hardcoded, because they vary per Jira site; and the draft is written as UTF-8-with-BOM CSV so Excel opens it correctly without mangling the identifier column.

## Technical Context

**Language/Version**: Python 3.11+ (`tomllib` for config, `ExceptionGroup`, modern typing). Chosen to match the repository's existing tooling — `.specify/init-options.json` sets `"script": "py"` and the Spec Kit scripts are Python — so contributors need one toolchain, not two.

**Primary Dependencies**:

- `typer` — CLI commands, arguments, and `--help`; chosen over bare `argparse` for the three-subcommand surface and typed options
- `httpx` — Jira REST calls; needs explicit timeouts, connection reuse, and a retry hook around `Retry-After`
- `pydantic` v2 — the draft row, run state, and generation output schemas, giving validation errors with field paths that FR-016 can report by row and column
- `anthropic` — the official SDK for the generation service, using structured outputs so the model returns schema-valid test cases rather than prose to parse
- `rich` — review prompt, progress, and the end-of-run result table
- `csv` (stdlib) — correct RFC 4180 quoting of multi-line cells; no third-party CSV library needed

**Storage**: Filesystem only; no database. One directory per run under a configurable workspace root (default `.jira-testgen/runs/<issue-key>-<timestamp>/`) holding `testcases.csv` (the editable draft) and `state.json` (authoritative run state and publication record).

**Testing**: `pytest` with `respx` to mock Jira HTTP at the transport layer, and a recorded-response fixture set for ADF parsing. The generation engine is tested against a stubbed client — no test calls the real model, because tests must not cost money or vary run to run.

**Target Platform**: Cross-platform CLI (Windows, macOS, Linux). Windows is the primary development environment here, which makes CSV encoding, `\r\n` line endings, and Excel round-tripping first-class concerns rather than afterthoughts.

**Project Type**: Single-project CLI tool with an importable package underneath, so the Jira and draft layers can be tested without going through the command line.

**Performance Goals**: Draft ready in under 2 minutes for a typical story (SC-001) — dominated by one streaming model call. Publishing 25 issues plus 25 links stays within Jira rate limits using sequential writes with backoff; parallel writes are deliberately rejected in [research.md](./research.md).

**Constraints**: Maximum 25 generated test cases per run (FR-011). No credential value may appear in output, logs, or draft (FR-027). Retries bounded at 5 total attempts (FR-025). Draft must survive a spreadsheet round-trip (SC-012). Distinct exit codes for scripted use (FR-028).

**Scale/Scope**: One Jira issue per run, at most 25 test cases, a single local user, no concurrency between runs beyond per-run directory isolation.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Status: no enforceable gates — `.specify/memory/constitution.md` is still the unmodified template.** Every principle in it is an unfilled placeholder (`[PRINCIPLE_1_NAME]`, `[SECTION_2_CONTENT]`, `[GOVERNANCE_RULES]`), so there are no ratified project principles to check this design against, and none can be inferred without inventing them. This is reported as a gap rather than a pass: the gate did not run because there was nothing to run it against.

**Pre-Phase 0**: Not applicable — no principles defined. **Post-Phase 1 re-check**: Not applicable, same reason.

The design independently adopts four practices that the constitution template's own commented examples suggest, so a later `/speckit-constitution` is likely to find the design already compliant:

| Practice adopted | Where it shows up in this design |
|---|---|
| Library-first (logic importable, CLI is a thin shell) | `cli.py` only parses and delegates; every layer is testable without a terminal |
| Text in / text out, JSON and human formats | `rich` tables for people, distinct exit codes and machine-readable state for scripts (FR-028) |
| Tests before implementation | `tests/contract/` fixes the CLI and CSV contracts before behavior is written |
| Observable and debuggable | Structured run log per run directory, with credential redaction (FR-027) |

**Recommendation**: run `/speckit-constitution` before `/speckit-implement`. The privacy posture settled during clarification — requirement text leaves the machine with no consent gate — is exactly the kind of decision a constitution normally constrains, and it is currently governed only by an assumption in the spec.

## Project Structure

### Documentation (this feature)

```text
specs/001-jira-test-case-generator/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── cli.md           # Command surface, arguments, exit codes
│   ├── draft-csv.md     # Draft CSV column contract and encoding rules
│   ├── jira-api.md      # Jira endpoints consumed, request/response shapes
│   └── generation.md    # Generation request and structured-output schema
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit-specify)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/jira_testgen/
├── __init__.py
├── __main__.py              # python -m jira_testgen
├── cli.py                   # Typer app: generate, approve, drafts
├── config.py                # Settings from env, then .env if present; credential redaction
├── errors.py                # Typed errors mapped to the exit codes in contracts/cli.md
├── models.py                # Pydantic: SourceRequirement, AcceptanceCriterion, TestCase, Draft, RunState
├── jira/
│   ├── __init__.py
│   ├── client.py            # httpx client: auth, timeouts, Retry-After backoff, redacted logging
│   ├── reader.py            # Fetch issue, discover AC field, list existing linked tests
│   ├── writer.py            # Permission precheck, create issues, create links
│   ├── adf.py               # ADF -> plain text, and test case -> ADF body
│   └── linktypes.py         # Discover and resolve the configured link type
├── generation/
│   ├── __init__.py
│   ├── engine.py            # Anthropic client, structured output, cap enforcement
│   ├── prompts.py           # System prompt and requirement framing
│   └── coverage.py          # Criterion -> test case traceability and shortfall notes
├── draft/
│   ├── __init__.py
│   ├── csv_io.py            # Read/write CSV (utf-8-sig), multi-line cell encoding
│   ├── validate.py          # FR-016 validation, reported by row and column
│   └── state.py             # state.json: run state + publication record, atomic writes
└── review.py                # Interactive prompt, subset selection, resume

tests/
├── contract/
│   ├── test_cli_contract.py
│   ├── test_draft_csv_contract.py
│   └── test_generation_schema.py
├── integration/
│   ├── test_generate_flow.py
│   ├── test_approve_publish_flow.py
│   ├── test_resume_after_interrupt.py
│   ├── test_partial_publish_retry.py
│   └── test_spreadsheet_roundtrip.py
└── unit/
    ├── test_adf.py
    ├── test_csv_io.py
    ├── test_validate.py
    ├── test_state.py
    ├── test_retry.py
    └── test_redaction.py
```

**Structure Decision**: Single project. The feature is one CLI tool with no server, no UI, and no second deployable, so the default single-project layout applies. The one structural choice worth naming is that `jira/`, `generation/`, and `draft/` are separate packages rather than flat modules: each is the seam where a different external contract lands (Jira's REST shapes, the model's output schema, the user's spreadsheet), and keeping them apart is what lets the test suite exercise CSV round-tripping and publish-retry logic without touching the network. The thin `cli.py` over an importable package is what makes that possible.

## Complexity Tracking

No constitution violations to justify — the Constitution Check above did not run, because no principles are defined. This table records the two places where the design is more complex than the obvious approach, so a reviewer can challenge them:

| Decision | Why Needed | Simpler Alternative Rejected Because |
|----------|------------|-------------------------------------|
| Run state in `state.json`, separate from the CSV draft | FR-023 and SC-007 require duplicate-free re-publishing after a partial failure, and FR-017 requires resuming a review in a later session | Keeping published issue keys only in a CSV column was rejected: FR-014 lets the user edit the file freely and a spreadsheet may rewrite it on save, so the one record that must survive cannot live in the file the user is invited to edit |
| An ADF builder plus a reverse text extractor | Jira Cloud v3 rejects a plain string for `description` and returns body fields as ADF trees, so both directions are required ([research.md](./research.md) R1) | Sending plain text was rejected because it fails with HTTP 400; using the v2 API for its wiki-markup strings was rejected because it is the older surface and the spec names Jira Cloud |
