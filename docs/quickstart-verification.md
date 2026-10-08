# Quickstart verification record

**Guide**: [quickstart.md](../specs/001-jira-test-case-generator/quickstart.md) · **Task**: T073
**Last run**: 2026-10-07, against the live Jira site `data-dc.atlassian.net`

Which quickstart scenarios have actually been executed, and against what.

> **Status**: the read-only half of T073 is **done against real Jira**. Everything that
> creates issues, and everything that needs the generation service, is **still unverified**
> — see [What is still blocked](#what-is-still-blocked).

---

## Environment

- Windows 11, Python 3.12, `pip install -e ".[dev]"`
- Credentials from a real `.env`; **live Jira Cloud site**, authenticated as a real account
- `ANTHROPIC_API_KEY` was still the `.env.example` placeholder, so no generation ran
- Visible projects: `OHRM` (OHrm), `SCRUM` (Autobots), `TC` (Teamwork Collection how-to)

## Test suite

| Check | Status | Result |
|---|---|---|
| `pytest`, no credentials set, no `.env` | ✅ | 746 passed |
| `pytest`, no credentials set, `.env` present | ✅ | 746 passed (T088) |
| `ruff check` / `format --check` | ✅ | clean |
| `mypy src/jira_testgen` | ✅ | clean, strict |

---

## Scenario 8 — error paths: 9 of 9 executed against real Jira ✅

Driven through the real CLI as a subprocess, with the token passed via the environment so
it never appears in a process listing.

| Case | Expected | Actual |
|---|---|---|
| `generate NOTAKEY` | `2` | ✅ `2` — no network call |
| `generate TC-1 --max-cases 99` | `2` | ✅ `2` — rejected, not clamped |
| `generate TC-999999` | `3` | ✅ `3` — HTTP 404 named |
| `generate` with an invalid token | `5` | ✅ `5` — **only after the fix below** |
| `--ac-field "No Such Field At All"` | `13` | ✅ `13` |
| `--link-type NoSuchLinkType` | `13` | ✅ `13` |
| `--issue-type NoSuchIssueType` | `13` | ✅ `13` — named project `TC` |
| `drafts` on an empty workspace | `0` | ✅ `0` |
| `approve` with no pending drafts | `9` | ✅ `9` |

Not yet covered, because each needs a specifically-arranged issue or project: exit `4`
(an issue the account cannot read), exit `6` (an issue with an empty description), exit `7`
(a project without create rights), exit `8` (a real-but-rejected generation key).

## Scenario 9 — credential leakage: executed with a real token ✅

18 output streams and run artifacts were searched for the real Jira token, a deliberately
invalid token, and the generation key. **No credential value appeared in any of them** —
stdout, stderr, `run.log`, `state.json`, or the draft.

This included the deliberately-invalid-token run, which is the case the scenario calls for:
the auth-failure path is where a traceback carrying an `Authorization` header would surface.

Also confirmed: `.env` is byte-identical before and after running commands (FR-026b), and
`md5sum` matches across a `drafts` invocation.

## Ingestion — verified against real ADF ✅

Not a numbered scenario, but the first time this code has seen a real Jira document. Against
`OHRM-9` and `OHRM-7`:

- Auth probe resolves a real account id
- Site config resolves on this site: link type `Relates` (inward "relates to"), issue type
  `Task` id `10051`
- ADF → text conversion produced 187 and 233 characters of correct prose
- **FR-009 confirmed working on real data**: `OHRM-9` contains an `inlineCard` node (a smart
  link), correctly reported in `unread_node_types` rather than silently dropped
- Criteria extraction found `AC-1` with `machine_readable=False` — correct, since the
  criteria are prose rather than a list, and that is what drives the SC-004 disclosure
- `mypermissions` on `OHRM`: `CREATE_ISSUES: true`, `LINK_ISSUES: true`

---

## Defect found and fixed by this run

**A bad token reported a configuration error instead of an auth failure (exit 13, not 5).**

On Atlassian Cloud, most read endpoints do **not** return 401 for an invalid token — they
serve the request anonymously and return `200` with an empty result. Verified directly on
this site:

| Endpoint | Good token | Bad token |
|---|---|---|
| `/rest/api/3/issueLinkType` | `200`, 4 link types | **`200`, 0 link types** |
| `/rest/api/3/myself` | `200` | `401` |

Because site configuration is resolved before the issue is fetched (contracts/cli.md step
2), the tool's first call was `/issueLinkType`. With a bad token it got `200` and an empty
list, so the user was told:

```
Error: This Jira site has no issue link type named 'Relates'.
  -> Available link types: (none returned).
```

Exit 13 — sending them to configure link types when their token was simply wrong. That is
exactly the confusion SC-008 exists to prevent, and contracts/cli.md is explicit that exit
5 must stay distinguishable.

**Fix**: `jira/permissions.py` gained `verify_authentication()`, a single `GET /myself`
probe that runs before site resolution. It treats both a `401` and a `200`-with-no-account
as exit 5. Regression tests are in `tests/integration/test_generate_failures.py`
(`TestAnonymousResponsesAreTreatedAsAuthFailures`). Re-running the harness afterwards:
exit `5`, message *"Jira rejected the credentials (HTTP 401)"*.

## Two other site behaviours worth knowing

- **This site rejects unbounded JQL**: `"Unbounded JQL queries are not allowed here."` The
  reconciliation queries in `jira/writer.py` are both bounded (`issue in linkedIssues(...)`
  and `project = ... AND summary ~ ...`), so they are unaffected — but a future query that
  forgets a restriction will fail on this site and pass on others.
- **The `.env` token had a stray leading `-`** (a paste artifact), which produced a 401.
  Worth knowing that this is the shape of the most likely user error: Atlassian tokens
  begin `ATATT`.

---

## What is still blocked

Two things, both needing a decision or a credential that only the account owner can supply.

### 1. `ANTHROPIC_API_KEY` is still the placeholder

`generate` cannot produce a draft, which blocks **Scenarios 1, 2, 3, 4, 5, 6 and 10** —
every scenario downstream of a generated draft. Set a real key in `.env` to unblock.

### 2. No scratch project has been nominated

The quickstart is explicit: *"Use a scratch project — these scenarios create real issues."*
The three visible projects all look like real work (`OHRM` is an OrangeHRM automation
project with live stories; `SCRUM` and `TC` hold example content). Scenario 2 and Scenario 5
would file up to 25 issues and link them.

**Nothing has been created in any project.** Set `JIRA_TARGET_PROJECT` to a throwaway
project before running the write scenarios.

### Order to work through once unblocked

1. **Scenario 1**, then open `testcases.csv` in a spreadsheet and check `test_id` still
   reads as `TC-...` and not a number or date. This is the one failure the whole CSV design
   exists to prevent, and the only way to see it is to look.
2. **Scenario 8's remaining rows** — exits `4`, `6`, `7`, `8`. Cheap, and they shake out
   site configuration before anything is created.
3. **Scenario 2.** Confirm from Jira that the issue body shows your *edited* wording and
   that the link appears from both sides.
4. **Scenario 5.** The important one. Interrupt a publish with Ctrl+C after a few issues
   appear, re-run `approve`, then count issues in Jira: the total must equal the approved
   case count with no repeated summary. Then the harder variant — edit a published row's
   summary and re-approve; it must not create a second issue.
5. **Scenario 6** with a real spreadsheet, and commit the saved files over the fixtures in
   `tests/fixtures/` (see that directory's README — the current ones are faithful
   reproductions, not real saves).
6. **Scenario 10** end to end with `--json | jq`.

The harness used for the read-only half is not checked in; it lives in the session
scratchpad. If you want it as a permanent `scripts/verify_quickstart.py`, say so — it would
make this record reproducible rather than narrative.

## Cleanup

Once the write scenarios have run, delete the created issues from the scratch project and
remove the run directories:

```bash
rm -rf .jira-testgen/runs/*
```
