# Contract: Command-Line Interface

**Feature**: [spec.md](../spec.md) | **Plan**: [plan.md](../plan.md)

The CLI is this feature's public interface. Changing a command name, an option name, an exit code, or the meaning of an exit code is a breaking change.

## Invocation

```text
jira-testgen generate <ISSUE-KEY> [options]
jira-testgen approve  [RUN-ID]    [options]
jira-testgen drafts               [options]
```

Also runnable as `python -m jira_testgen <command>`.

---

## `generate` — ingest, generate, pause for review

Implements FR-001 through FR-013 and FR-029 through FR-031.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `<ISSUE-KEY>` | arg, required | — | Source Jira issue, e.g. `PROJ-123` |
| `--target-project` | str | source project | Project to file test cases in (FR-021) |
| `--ac-field` | str | from config | Name of the acceptance criteria field (research R5) |
| `--issue-type` | str | from config | Issue type for created test cases |
| `--link-type` | str | from config | Link relationship name |
| `--max-cases` | int | `25` | Hard cap; values above 25 are rejected, not clamped silently (FR-011) |
| `--workspace` | path | `.jira-testgen` | Run directory root |
| `--no-wait` | flag | off | Write the draft and exit instead of waiting at the prompt |
| `--json` | flag | off | Machine-readable result on stdout; human output suppressed |
| `--yes` | flag | off | Approve the generated draft without review. **Bypasses the FR-013 safety gate** |

### Behavior

1. Validate the issue key shape locally. Malformed → exit `2`, no network call.
2. Resolve config and credentials; resolve and validate the AC field, issue type, and link type against the site (research R5) **before** spending a model call.
3. Fetch the issue. Not found → `3`. No read permission → `4`. Auth failure → `5`.
4. If the requirement content is empty or too sparse → `6`, and write no draft (FR-004).
5. Report any already-linked test cases (FR-005).
6. Verify `CREATE_ISSUES` and `LINK_ISSUES` on the target project (FR-022). Missing → `7`.
7. Generate test cases. Service failure or unusable output → `8`, and write no partial draft (FR-031).
8. Write `testcases.csv` and `state.json`; print the draft path.
9. Unless `--no-wait`, wait at the review prompt. On approval, continue into the publish sequence described under `approve`.

`--yes` exists for CI and for users who have accepted the risk; it is not the default and the help text says what it bypasses. It does not skip validation — an unparseable or empty draft still fails.

---

## `approve` — review an existing draft and publish

Implements FR-014 through FR-024. This is the resume path (FR-017).

| Option | Type | Default | Meaning |
|---|---|---|---|
| `[RUN-ID]` | arg, optional | most recent pending run | Which draft to act on (FR-017a) |
| `--reject` | flag | off | Mark the draft rejected; create nothing (FR-015) |
| `--only` | str | all approved | Comma-separated `test_id` list to publish (FR-015 subset) |
| `--dry-run` | flag | off | Validate and report what would be created; touch nothing in Jira |
| `--json` | flag | off | Machine-readable result |

### Behavior

1. Resolve the run. No pending drafts → `9`. An ambiguous bare invocation with several pending drafts → `9` with the list, rather than guessing (FR-017a).
2. Re-read the CSV from disk — the user's edits are the input (FR-014).
3. Validate. Failures are reported by row number and column name, and the command exits `10` without publishing, leaving the draft intact so the user can fix and re-approve (FR-016).
4. Nothing approved → exit `0` with "nothing to publish" (FR-018). This is a success, not a failure.
5. Skip any `test_id` that already has a recorded issue key (FR-023).
6. For each outstanding case: create the issue, record the issue key immediately, then create the link and record that separately (research R4).
7. Repair any entry with an issue key but no link, before creating new cases.
8. Print every created issue key with its URL, plus every failure and its reason (FR-024).

---

## `drafts` — list drafts awaiting review

Implements FR-017a. Lists run ID, issue key, case count, phase, age, and published-so-far count. `--json` for machine use. Exit `0` even when the list is empty — an empty list is an answer.

---

## Exit codes

Distinct codes are a requirement, not a convenience: FR-028 requires scripted workflows to distinguish outcomes, and SC-008 requires every user-reachable failure to be identifiable.

| Code | Meaning | Spec |
|---|---|---|
| `0` | Success, including "nothing to publish" and an empty `drafts` list | FR-018, FR-028 |
| `1` | Unexpected internal error | — |
| `2` | Malformed issue key or invalid arguments | FR-003 |
| `3` | Issue not found | FR-003 |
| `4` | No permission to read the issue | FR-003 |
| `5` | Authentication failure | FR-003 |
| `6` | Requirement content empty or too sparse | FR-004 |
| `7` | Missing `CREATE_ISSUES` or `LINK_ISSUES` on the target project | FR-022 |
| `8` | Generation service unreachable, refused, or returned unusable output | FR-031 |
| `9` | No pending draft, or an ambiguous run selection | FR-017a |
| `10` | Draft failed validation | FR-016 |
| `11` | Publish partially or wholly failed; re-runnable | FR-024 |
| `12` | Retries exhausted against Jira | FR-025 |
| `13` | Site configuration unresolved (unknown AC field, issue type, or link type) | research R5 |

Codes `3`, `4`, and `5` are separate on purpose. A single "Jira error" code would make it impossible for a user — or a script — to tell a typo in the issue key from an expired token, which is precisely the confusion SC-008 is written to prevent.

## Output rules

- Human output goes to stdout; diagnostics and errors to stderr.
- `--json` emits one JSON object on stdout and suppresses decorative output, so stdout stays parseable.
- No credential value ever appears in either stream, in any mode, including tracebacks (FR-027).
- Every error message names what went wrong and what to do next (SC-008).
