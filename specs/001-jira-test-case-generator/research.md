# Phase 0 Research: Jira Test Case Generator

**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Date**: 2026-10-06

This document resolves the unknowns in the plan's Technical Context. Items marked **Verified** were checked against current documentation during planning; items marked **Decided** are design choices with no external fact to verify.

---

## R1. Jira Cloud body fields are ADF, not text — **Verified**

**Decision**: Build both directions of an Atlassian Document Format (ADF) converter. Outbound, compose a `{"type": "doc", "version": 1, "content": [...]}` tree for the test case body. Inbound, walk the description ADF tree and extract text, recording any node type that carries no text equivalent.

**Rationale**: Jira Cloud REST API v3 requires ADF for body fields such as `description` and comment bodies. Sending a plain string returns HTTP 400 on every create attempt, so this is not a nicety — the feature cannot write an issue at all without it. The inbound direction matters just as much: descriptions come back as ADF trees, so FR-002's "retrieve the description" means walking a tree, and the spec's edge case about tables, images, and attachments is really a question about which ADF node types the extractor understands.

**Design consequence**: `jira/adf.py` owns both directions. The extractor handles `paragraph`, `text`, `heading`, `bulletList`/`orderedList`/`listItem`, `codeBlock`, `blockquote`, `table`, and `hardBreak`/`rule`. Anything else — `mediaSingle`, `mediaGroup`, `inlineCard`, `embedCard`, `extension` — is collected into a list of unread node types that FR-009 reports in the draft, satisfying the spec's requirement that non-textual content be "noted as unread rather than silently dropped".

**Alternatives considered**:

- **Send plain text to v3** — rejected: returns HTTP 400.
- **Use REST API v2, which accepts wiki-markup strings** — rejected: v2 is the older surface, the spec names Jira Cloud explicitly, and the wiki-markup round trip has its own escaping problems. Not worth adopting a legacy API to avoid one converter.
- **Use a third-party ADF library** — rejected for now: the node set above is small and stable, and a hand-written builder keeps the dependency surface honest. Revisit only if the extractor starts accumulating special cases.

---

## R2. Permission precheck before any write — **Verified**

**Decision**: Before creating anything, call `GET /rest/api/3/mypermissions` with both `projectKey=<target>` and `permissions=CREATE_ISSUES,LINK_ISSUES`, and refuse to proceed unless both come back granted.

**Rationale**: FR-022 requires verifying the account can create issues *and* links before creating anything, and naming the missing permission. The `permissions` query parameter has been mandatory since February 2019 — a request without it is rejected, so the obvious "just ask for everything" call does not work. Scoping with `projectKey` is what makes the answer meaningful, since permissions are per project and the target project may differ from the source project (FR-021).

**Design consequence**: `jira/writer.py` runs this check as a gate, not as advice. Checking `LINK_ISSUES` separately from `CREATE_ISSUES` matters: an account that can create issues but not link them would otherwise produce orphan test cases that satisfy nothing in the spec — FR-020 requires the link, so a missing `LINK_ISSUES` is a hard stop, not a degraded mode.

**Alternatives considered**:

- **Skip the precheck and let the first create fail** — rejected: FR-022 requires stopping *before* creating anything. Discovering the problem on test case 1 of 25 leaves a partial mess that the user then has to reconcile.
- **Check only `CREATE_ISSUES`** — rejected: silently produces unlinked test cases.

---

## R3. Rate limiting and retry policy — **Verified**

**Decision**: Treat both HTTP 429 and any 5xx carrying a `Retry-After` header as rate limiting. Honour `Retry-After` when present. When absent, back off exponentially with jitter (`base 1s × 2^attempt` plus random jitter). Cap at 4 retries, 5 attempts total, then stop and report that retrying was abandoned.

**Rationale**: Atlassian's own guidance is that rate limiting shows up as a 429 *or* a 5xx accompanied by `Retry-After`, that `Retry-After` should always be respected when present, and that a sensible ceiling is 4 retries. Jitter is specifically called out to avoid synchronized retry storms. This also quantifies FR-025, which the spec deliberately left as "a bounded number of attempts".

**Design consequence**: The retry policy lives in `jira/client.py` as a transport-level concern, so every call inherits it rather than each call site remembering. Retry budget is per request, not per run. `tests/unit/test_retry.py` asserts the `Retry-After` path, the jittered-backoff path, and that exhaustion raises a distinct error with its own exit code — because FR-025 requires reporting clearly when it stops retrying, which a generic failure message would not do.

**Alternatives considered**:

- **Retry forever until it succeeds** — rejected: a CLI that never terminates is worse than one that fails with an explanation.
- **Retry on every 5xx regardless of `Retry-After`** — rejected as the default for writes. A 500 on issue creation may mean the issue was created, so blind retries risk the duplicates SC-007 forbids. Writes retry only on 429 and on 5xx with `Retry-After`; see R4 for how a genuinely ambiguous write is handled.

---

## R4. Duplicate-free publishing without server-side idempotency — **Decided**

**Decision**: Keep the authoritative publication record in `state.json`, keyed by test case identifier, and write each `(identifier → issue key)` entry to disk immediately after the create succeeds — before the link is attempted and before moving to the next case. On re-publish, skip any identifier that already has a recorded issue key. For a write whose outcome is genuinely unknown (connection dropped after the request was sent), do not retry blindly: search Jira for an issue already linked to the source requirement whose summary matches, and reconcile before deciding.

**Rationale**: Jira's create-issue endpoint has no client-supplied idempotency key, so the tool cannot ask the server "did this already happen?" by token. The only reliable defence against the duplicates SC-007 forbids is a durable local record written at the right moment. Writing it after each individual create — rather than batching at the end — is what bounds the damage from a crash to at most one ambiguous case instead of all 25.

**Design consequence**: `draft/state.py` writes atomically (temp file plus `os.replace`) so a crash mid-write cannot leave truncated JSON and lose the whole record. Links are recorded separately from issue creation, because the two-step create-then-link sequence can fail between the steps; a recorded issue with no recorded link is resumable by creating only the missing link.

**Alternatives considered**:

- **Store published issue keys in a CSV column** — rejected, and this is the single most important rejection in the design. FR-014 explicitly invites the user to edit the draft, and R6 shows spreadsheet applications rewrite these files on save. The one record that must survive cannot live in the file the user is told to edit freely.
- **Query Jira for existing links on every publish instead of keeping state** — rejected as the primary mechanism: it cannot distinguish a test case this tool created from one a human linked, and it would re-read on every run. Retained only as the reconciliation path for an ambiguous write, where its weakness does not matter because a human is already being asked.

---

## R5. Discovering site-specific fields and link types — **Decided**

**Decision**: Resolve three site-specific things at runtime rather than hardcoding them: the acceptance criteria field (via `GET /rest/api/3/field`, matched by configured name, falling back to the description), the issue link type (via `GET /rest/api/3/issueLinkType`, matched by configured name, defaulting to a "Relates" style link), and the test case issue type (validated against the target project's create metadata before use).

**Rationale**: The spec's assumptions already commit to all three being configurable because they vary per Jira site. Validating them up front converts a confusing mid-run failure — Jira rejecting an unknown field or link type with a terse message — into a clear startup error that names what was not found and lists what is available.

**Design consequence**: `jira/linktypes.py` and the field lookup in `jira/reader.py` both report what they searched for and what exists, so a misconfigured site produces an actionable message. Resolution happens before generation, not before publishing, so the user is not told about a bad link type only after spending a model call.

**Alternatives considered**:

- **Hardcode `customfield_10000` style IDs** — rejected: custom field IDs differ per site, so this breaks on any site but the author's.
- **Hardcode the link type name `"Relates"`** — rejected: sites rename and remove link types. The default is a "Relates" style link, but it is resolved and verified, not assumed.

---

## R6. Excel-safe CSV that survives a round trip — **Decided**

**Decision**: Write the draft as UTF-8 **with** BOM (`utf-8-sig`), comma-delimited, with `QUOTE_MINIMAL` quoting from the stdlib `csv` module and `\r\n` line terminators. Encode multi-step content inside a single cell as newline-separated numbered lines. Prefix the identifier with a non-numeric marker so no spreadsheet coerces it to a number or date. On read, accept UTF-8 with or without BOM, sniff the delimiter between comma and semicolon, and normalise line endings.

**Rationale**: FR-012a requires the draft to open correctly in a spreadsheet application *and* stay editable as text, and SC-012 requires a spreadsheet round trip to preserve step order and identifiers. Each rule above targets a specific documented failure: Excel misreads UTF-8 without a BOM and mangles non-ASCII; it writes a semicolon delimiter under some locales; and it eagerly reformats values that look numeric, which would silently corrupt the one field R4 depends on. The stdlib `csv` module already quotes embedded newlines, commas, and quotes correctly per RFC 4180 — this is the one part that needs no cleverness, only correct use.

**Design consequence**: `draft/csv_io.py` owns all of this, and `tests/integration/test_spreadsheet_roundtrip.py` asserts it against fixture files saved by a spreadsheet application rather than only files the tool wrote itself — a test that reads back only its own output would pass while the real failure mode persists.

**Honest tradeoff**: CSV was chosen by the user during clarification over Markdown and YAML. It interoperates with existing CSV-based test case tooling and lets non-technical reviewers work in a spreadsheet, which is a real benefit. The cost is this section: multi-step content lives in single cells, and the format invites a class of silent corruption that a Markdown draft would not have. The mitigations above contain it; they do not eliminate it, which is why SC-012 exists as a standing test rather than a one-time check.

**Alternatives considered**:

- **Plain UTF-8 without BOM** — rejected: Excel mangles non-ASCII characters in requirement text.
- **One row per step instead of per test case** — rejected: it would make the identifier non-unique per row and complicate FR-023's keying, trading a formatting problem for a correctness problem.
- **Separate metadata sidecar for identifiers, keeping them out of the CSV** — partially adopted. `state.json` is that sidecar for the publication record (R4), but the identifier still appears in the CSV, because the user needs to see which row maps to which published issue.

---

## R7. Generation engine: model, structured output, and cap enforcement — **Verified**

**Decision**: Call the Claude API through the official `anthropic` Python SDK using model `claude-opus-5-5`, with adaptive thinking (`thinking: {"type": "adaptive"}`), explicit `output_config: {"effort": "high"}`, streaming, and structured outputs (`output_config.format`) carrying a JSON schema for the test case list. Enforce the 25-case cap in code after parsing, not only in the prompt.

**Rationale**: `claude-opus-5-5` is the current default Opus model; its effort default is `medium`, one level below the rest of the family, so leaving it unset would quietly under-resource a task that benefits from careful reasoning — deriving negative and edge cases from prose is exactly that. Structured outputs remove the entire class of bugs where the model returns well-written prose that the parser then has to guess at; the schema makes FR-006's required fields a parse-time guarantee rather than a hope. Streaming is used because a 25-case draft is a long output and non-streaming requests risk HTTP timeouts.

**Design consequence**: `generation/engine.py` validates the parsed output against the same Pydantic models the rest of the code uses, so a schema-valid but semantically empty result (a test case with zero steps) still fails FR-006 and is reported as the unusable-output case in FR-031. The cap is enforced in code because a prompt instruction is a request, not a constraint — and FR-011 requires the limit to hold.

**Alternatives considered**:

- **A cheaper model (`claude-sonnet-5-5`, `claude-haiku-4-5`)** — rejected as the default. Test case quality is the product here: SC-003 asks that 80% of generated cases publish with no more than wording edits, and SC-005 asks that 30% be negative or edge cases. Downgrading to save per-run cost trades the feature's main quality metric for pennies, and that is the user's call to make explicitly, not a default to assume.
- **Forced tool use to coerce JSON** — rejected: `tool_choice` `any`/`tool` returns a 400 on this model. Structured outputs are the supported mechanism.
- **Prompt-only "return JSON" instruction with hand-rolled parsing** — rejected: strictly worse than structured outputs, and it reintroduces the parse-failure path that FR-031 would then have to absorb.

---

## R8. Credential handling and redaction — **Decided**

**Decision**: Read credentials from environment variables (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `ANTHROPIC_API_KEY`), **and from a `.env` file in the working directory when one is present**, with the environment taking precedence. Never write a secret to the draft, `state.json`, the run log, or the terminal. Install a redaction filter on logging keyed on the live secret values, and assert this in tests.

> **Amended 2026-10-07 — `.env` support added, reversing the rejection below.** This section
> originally rejected any file-based credential source. That rejection is overturned (see
> spec Clarifications, Session 2026-10-07) on three grounds:
>
> 1. The stated objection was "a token in a working-directory file gets committed". The
>    project's `.gitignore` already excludes `.env` and `.env.*`, so the specific failure
>    the rejection guarded against is addressed by a mechanism that did not exist when the
>    decision was written.
> 2. The spec's own Assumptions already permitted "a local configuration the user
>    controls", so the original decision was narrower than the spec it was serving — an
>    inconsistency, not a deliberate tightening.
> 3. The alternative in practice is `export JIRA_API_TOKEN=...` in a shell, which writes
>    the token to shell history in plaintext. That is not safer; it is the same exposure
>    somewhere users think to look less often.
>
> The risk is not eliminated. A plaintext token on disk can still leave via a copied
> directory, a container image layer, or `git add -f`. The mitigations are narrow and
> enumerated in FR-026b: read-only, environment wins, never logged, `.env.example` holds
> placeholders only. The residual risk is recorded in the spec's Assumptions rather than
> claimed away.

**Rationale**: FR-027 forbids credentials in output of any kind. The realistic leak is not someone printing a token deliberately — it is an HTTP debug log, an exception repr carrying request headers, or a traceback. A filter keyed on the actual secret values catches all three paths, where a rule like "don't log headers" catches only the one the author remembered.

**Design consequence**: `config.py` owns secret resolution and exposes values through a type whose `__repr__` and `__str__` are redacted, so a stray f-string or a traceback cannot leak one. `tests/unit/test_redaction.py` asserts that a forced exception carrying auth headers produces no secret substring in the captured output.

**Alternatives considered**:

- **Config file holding the API token** — originally rejected on the grounds that a token in a working-directory file gets committed. **Overturned** by the amendment above: a gitignored `.env`, read-only and never logged, is now supported. A *committed* config file holding a token remains rejected, which is what the ignore rule and the placeholders-only `.env.example` enforce.
- **Trusting that no code path logs headers** — rejected: unenforceable, and FR-027 is absolute.
- **An explicit `--env-file <path>` flag instead of implicit loading** — rejected: it makes the common case (one project, one `.env`) require a flag on every invocation, and users who forget it get a confusing "variable not set" error while a perfectly good file sits next to them. Implicit loading of a single conventional filename, with the environment still winning, is the behaviour users already expect from this pattern.
- **A non-secret-settings-only config file** — rejected as insufficient: it was the original plan, and it does not address the actual friction, which is re-entering four credentials in every new terminal session.

---

## Resolved unknowns summary

| Unknown from Technical Context | Resolution | Status |
|---|---|---|
| How Jira body fields are represented | ADF trees both directions (R1) | Verified |
| How to satisfy the pre-write permission gate | `mypermissions` with `projectKey` + explicit `permissions` list (R2) | Verified |
| What "bounded retries" means concretely | `Retry-After`, else jittered backoff; 5 attempts (R3) | Verified |
| How to guarantee no duplicate issues | Durable per-identifier publication record, written per create (R4) | Decided |
| How to handle per-site field and link type variation | Runtime discovery and validation before generation (R5) | Decided |
| How to keep CSV safe through a spreadsheet edit | `utf-8-sig`, minimal quoting, non-numeric identifiers, tolerant reader (R6) | Decided |
| Which model and output mode for generation | `claude-opus-5-5`, adaptive thinking, high effort, structured outputs, streaming (R7) | Verified |
| How to guarantee no credential leakage | Env-var secrets, redacting type, logging filter, asserted in tests (R8) | Decided |

No `NEEDS CLARIFICATION` markers remain in the Technical Context.

## Sources

- [Jira Cloud platform REST API — permission schemes and `mypermissions`](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-permission-schemes/)
- [Change notice: Get my permissions requires the `permissions` query parameter](https://developer.atlassian.com/cloud/jira/platform/change-notice-get-my-permissions-requires-permissions-query-parameter/)
- [Atlassian — API rate limit handling for apps](https://www.atlassian.com/blog/development/api-rate-limit-handling-for-apps)
- [Atlassian developer — rate limiting and retries](https://developer.atlassian.com/platform/app-migration/rate-limiting-and-retries/)
- Claude API reference (bundled `claude-api` skill): current model IDs, adaptive thinking, effort defaults, structured outputs via `output_config.format`, streaming for large `max_tokens`
