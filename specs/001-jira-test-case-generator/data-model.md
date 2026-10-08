# Data Model: Jira Test Case Generator

**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Date**: 2026-10-06

All entities are in-process Pydantic models plus two on-disk artifacts per run. There is no database. The two artifacts have different owners and different trust levels, which is the single most important thing about this model:

| Artifact | Owner | Trust | Why |
|---|---|---|---|
| `testcases.csv` | The user | Untrusted — re-validated on every read | FR-014 invites free editing; a spreadsheet may rewrite it (research R6) |
| `state.json` | The tool | Authoritative | FR-023 and SC-007 depend on it surviving exactly as written (research R4) |

---

## Entity: SourceRequirement

The Jira issue the run is based on. Read once, then frozen — later steps work from this snapshot, never by re-reading Jira.

| Field | Type | Rules |
|---|---|---|
| `issue_key` | str | Matches `^[A-Z][A-Z0-9_]+-[0-9]+$`; validated before any network call (FR-003) |
| `summary` | str | Non-empty |
| `description_text` | str | Plain text extracted from the description ADF tree (research R1) |
| `acceptance_criteria_text` | str \| None | From the discovered AC field, or `None` when it lives in the description |
| `fields_read` | list[str] | Field names actually read, recorded in the draft (FR-002) |
| `unread_node_types` | list[str] | ADF node types with no text equivalent — images, media, embeds (FR-009) |
| `existing_linked_tests` | list[LinkedIssueRef] | Test cases already linked, reported before generating (FR-005) |
| `project_key` | str | Source project; may differ from the publish target (FR-021) |
| `read_at` | datetime | UTC; lets the user judge staleness at approval time |

**Validation**: `description_text` and `acceptance_criteria_text` must not both be empty or whitespace — that is the insufficient-content stop in FR-004, raised before any model call is made and before any draft is written.

---

## Entity: AcceptanceCriterion

A single discrete condition extracted from the requirement. The unit coverage is measured against (FR-007).

| Field | Type | Rules |
|---|---|---|
| `criterion_id` | str | `AC-1`, `AC-2`, … Stable within a run |
| `text` | str | Non-empty, verbatim from the source where possible |
| `source_field` | str | Which field it came from, for traceability |
| `machine_readable` | bool | `False` when criteria were prose rather than a list — drives the SC-004 shortfall disclosure |

---

## Entity: TestCase

One manual test. The identifier is the load-bearing field.

| Field | Type | Rules |
|---|---|---|
| `test_id` | str | Stable identifier, unique within the draft. Format `TC-<run>-<nnn>`, deliberately non-numeric so no spreadsheet coerces it (research R6). Assigned at generation; never reassigned (FR-006a) |
| `summary` | str | 1–255 chars — Jira's summary limit, enforced locally so a 25-case publish cannot fail on case 20 for a reason knowable at validation time |
| `preconditions` | str | May be empty; a test with no setup is legitimate |
| `steps` | list[str] | At least 1, each non-empty (FR-006) |
| `expected_results` | list[str] | At least 1. Either one per step (same length as `steps`) or exactly one overall |
| `traces_to` | list[str] | `criterion_id` values, or `["REQ"]` when motivated by the description rather than a specific criterion (FR-007) |
| `case_kind` | enum | `positive` \| `negative` \| `edge` — makes the 30% floor in SC-005 measurable rather than a matter of opinion |
| `approval` | enum | `pending` \| `approved` \| `rejected`. Defaults to `pending`; only `approved` publishes (FR-015) |
| `origin` | enum | `generated` \| `user_added`. A row the user typed has no identifier until validation assigns one (FR-023a) |

**Validation rules**

1. `test_id` unique across the draft — a duplicate is a hard rejection, reported with both row numbers (FR-016).
2. `len(expected_results)` is either `1` or `len(steps)`; anything else is a validation error naming the row.
3. `steps` non-empty after stripping whitespace — catches the schema-valid-but-empty generation result (FR-031).
4. A row with blank `test_id` is accepted as `origin=user_added` and assigned an identifier; a row with a *changed* identifier is indistinguishable from a new one and is treated as new, which is why FR-006a tells the user not to edit it.

### State transitions

```text
                  generation
                      │
                      ▼
                  [pending] ──── user rejects ────▶ [rejected] ──▶ (never published)
                      │
                 user approves
                      │
                      ▼
                 [approved] ──── publish succeeds ───▶ [published]   (issue key recorded)
                      │
                      └──────── publish fails ──────▶ [approved]     (retried next run)
```

`published` is not a column in the CSV — it is derived from the presence of a publication record entry for that `test_id`. This is deliberate: the user can edit the CSV, so publication status must not be something they can accidentally flip by typing in a cell.

---

## Entity: Draft

The reviewable artifact: a CSV table plus the header context the reviewer needs.

| Field | Type | Rules |
|---|---|---|
| `run_id` | str | `<ISSUE-KEY>-<YYYYMMDD-HHMMSS>`; names the run directory |
| `source` | SourceRequirement | The snapshot the cases were generated from |
| `test_cases` | list[TestCase] | 1–25 (FR-011) |
| `coverage_notes` | list[str] | Uncovered criteria, unread ADF nodes, cap-reached disclosure (FR-009) |
| `generation_service` | str | Which service and model produced the cases (FR-030) |
| `generated_at` | datetime | UTC |

**Validation**: at most 25 `test_cases`; every `criterion_id` with `machine_readable=True` appears in at least one case's `traces_to`, or a `coverage_notes` entry explains the gap (FR-007, SC-004).

The CSV serialization — exact columns, encoding, multi-line cell encoding — is a contract in its own right and lives in [contracts/draft-csv.md](./contracts/draft-csv.md). The `source`, `coverage_notes`, and `generation_service` fields are written as a commented preamble above the header row, so the reviewer sees the provenance without opening a second file.

---

## Entity: RunState (`state.json`)

Authoritative, tool-owned, written atomically. This is what makes the review pause survivable and re-publishing safe.

| Field | Type | Rules |
|---|---|---|
| `schema_version` | int | Starts at `1`; lets a future version refuse or migrate an old run rather than misread it |
| `run_id` | str | Matches the directory name |
| `phase` | enum | `drafted` \| `approved` \| `publishing` \| `published` \| `rejected` \| `failed` |
| `source_snapshot` | SourceRequirement | So resume never re-reads Jira (FR-017) |
| `draft_path` | str | Path to `testcases.csv` |
| `target_project_key` | str | Resolved at generation so approval cannot silently retarget |
| `issue_type_id` / `link_type_name` | str | Resolved and validated site values (research R5) |
| `publication_record` | dict[str, PublicationEntry] | Keyed by `test_id` — the duplicate guard (FR-023) |
| `generation_meta` | dict | Service, model, timestamp, token usage. No credentials (FR-027) |
| `created_at` / `updated_at` | datetime | UTC |

### Entity: PublicationEntry

| Field | Type | Rules |
|---|---|---|
| `test_id` | str | The key |
| `issue_key` | str \| None | Set the moment the create succeeds, before the link is attempted |
| `issue_url` | str \| None | For the end-of-run report (FR-024) |
| `linked` | bool | Tracked separately — create and link are two calls that can fail between |
| `created_at` | datetime | |
| `last_error` | str \| None | Redacted failure text for the report |

**Why `linked` is separate**: creating the issue and linking it are two API calls. A crash between them leaves a real issue with no link. Recording them separately lets a resume create only the missing link instead of either duplicating the issue or leaving FR-020 unsatisfied forever.

**Atomicity**: every mutation writes to a temp file in the same directory and then `os.replace`s it, so an interrupted write cannot truncate the record. A record written after each individual create — not batched at the end — bounds crash damage to one ambiguous case (research R4).

---

## Entity: LinkedIssueRef

A lightweight reference to an issue already linked to the requirement, used for the FR-005 pre-generation report.

| Field | Type |
|---|---|
| `issue_key` | str |
| `summary` | str |
| `link_type` | str |
| `issue_type` | str |

---

## Relationships

```text
SourceRequirement ──1:N──▶ AcceptanceCriterion
         │                         ▲
         │ 1:1                     │ traces_to (N:M)
         ▼                         │
       Draft ──────1:N────────▶ TestCase
         │                         │
         │ 1:1                     │ 1:0..1
         ▼                         ▼
      RunState ───1:N────▶ PublicationEntry ──▶ Jira issue

SourceRequirement ──1:N──▶ LinkedIssueRef   (pre-existing, read-only)
```

`TestCase → PublicationEntry` is `1:0..1`: a case has at most one published issue, ever. That cardinality *is* SC-007 — no run may produce a second issue for the same `test_id`.

---

## Derived values (never stored)

| Value | Derived from | Used by |
|---|---|---|
| `published` status of a case | `test_id` present in `publication_record` with non-null `issue_key` | Skip logic on re-publish (FR-023) |
| Outstanding cases to publish | approved cases minus published ones | FR-023 |
| Missing links to repair | entries with `issue_key` set and `linked == False` | FR-020 on resume |
| Negative/edge proportion | `case_kind` counts | SC-005 |
| Criteria coverage shortfall | criteria minus union of `traces_to` | FR-009, SC-004 |

These are computed rather than stored because a stored copy can disagree with the record it summarizes, and when it does, the duplicate guard is what breaks.
