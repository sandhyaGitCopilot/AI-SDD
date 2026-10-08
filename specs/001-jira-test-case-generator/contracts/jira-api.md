# Contract: Jira Cloud REST API Usage

**Feature**: [spec.md](../spec.md) | **Research**: [research.md](../research.md)

Every Jira endpoint this feature depends on, with the shape it is called with and the failures it must handle. `tests/` mocks these at the transport layer with `respx`; no test touches a real Jira site.

**Base**: `https://<site>.atlassian.net/rest/api/3`
**Auth**: HTTP Basic — account email plus API token, resolved from the environment or from a `.env` file in the working directory, with the environment taking precedence (FR-026a, research R8). The header value is never logged, whichever source supplied it.

---

## Reads

### 1. Fetch the source issue — FR-002

```http
GET /rest/api/3/issue/{issueKey}?fields=summary,description,issuelinks,issuetype,project,{acFieldId}
```

`description` comes back as an ADF tree, not a string, and is converted by the extractor (research R1). `issuelinks` supplies the already-linked test cases for the FR-005 report.

| Response | Handling | Exit |
|---|---|---|
| `200` | Build `SourceRequirement` | — |
| `401` | Authentication failure — distinct from a permission problem | `5` |
| `403` | No permission to read this issue | `4` |
| `404` | Not found, **or** not visible to this account — Jira does not distinguish, so the message must say both are possible | `3` |
| `429` / `5xx` + `Retry-After` | Retry per policy | `12` on exhaustion |

### 2. Discover the acceptance criteria field — research R5

```http
GET /rest/api/3/field
```

Match the configured field name case-insensitively against `name`, take its `id`, and add it to the issue fetch. No match → fall back to the description and record that in `fields_read`; an explicitly configured name that does not exist is exit `13`, because silently ignoring it would make the AC field look empty rather than misconfigured.

### 3. Resolve the link type — research R5

```http
GET /rest/api/3/issueLinkType
```

Match the configured name against `name`, `inward`, and `outward`. No match → exit `13`, listing what the site does have.

### 4. Validate the issue type against the target project

```http
GET /rest/api/3/issue/createmeta?projectKeys={target}&expand=projects.issuetypes.fields
```

Confirms the configured test case issue type exists in the target project and reports which fields are mandatory there. Run **before** generation, so a site whose project requires a field the tool does not populate fails in seconds rather than after a model call.

---

## Permission precheck

### 5. Verify create and link rights — FR-022

```http
GET /rest/api/3/mypermissions?projectKey={target}&permissions=CREATE_ISSUES,LINK_ISSUES
```

The `permissions` parameter is **mandatory** — a request without it is rejected, so this cannot be called bare (research R2).

```json
{
  "permissions": {
    "CREATE_ISSUES": { "havePermission": true },
    "LINK_ISSUES":   { "havePermission": false }
  }
}
```

Both must be `true`. `LINK_ISSUES: false` is a hard stop, not a degraded mode: FR-020 requires the link, so proceeding would produce orphan issues that satisfy nothing. The error names the project and the specific missing permission.

---

## Writes

### 6. Create a test case issue — FR-019

```http
POST /rest/api/3/issue
```

```json
{
  "fields": {
    "project":   { "key": "QA" },
    "issuetype": { "id": "10003" },
    "summary":   "Password reset rejects an unregistered email",
    "description": {
      "type": "doc",
      "version": 1,
      "content": [
        { "type": "heading", "attrs": { "level": 3 },
          "content": [ { "type": "text", "text": "Preconditions" } ] },
        { "type": "paragraph",
          "content": [ { "type": "text", "text": "A user account exists." } ] },
        { "type": "heading", "attrs": { "level": 3 },
          "content": [ { "type": "text", "text": "Steps" } ] },
        { "type": "orderedList", "content": [
          { "type": "listItem", "content": [
            { "type": "paragraph", "content": [ { "type": "text", "text": "Open the reset page" } ] } ] } ] }
      ]
    }
  }
}
```

`description` **must** be an ADF document. A plain string returns `400` (research R1).

| Response | Handling | Exit |
|---|---|---|
| `201` | Record `issue_key` in the publication record **immediately**, before linking (research R4) | — |
| `400` | Field rejected — report the field and stop; retrying cannot help | `11` |
| `403` | Lost permission mid-run | `7` |
| `429` / `5xx` + `Retry-After` | Retry per policy; on exhaustion, leave the record resumable | `12` |
| Connection dropped after send | **Do not blindly retry** — outcome unknown. Reconcile per research R4 | `11` |

### 7. Link the test case to the requirement — FR-020

```http
POST /rest/api/3/issueLink
```

```json
{
  "type":         { "name": "Relates" },
  "inwardIssue":  { "key": "QA-456" },
  "outwardIssue": { "key": "PROJ-123" }
}
```

Returns `201` with an empty body. Recorded as `linked: true` separately from issue creation, because a crash between the two calls leaves a real issue with no link — recoverable only if the two facts are tracked apart. Works across projects, which is what makes FR-021 possible.

### 8. Find existing test cases for reconciliation — research R4

```http
GET /rest/api/3/search/jql?jql=issue in linkedIssues("PROJ-123")&fields=summary,issuetype
```

Used only on the ambiguous-write path, to decide whether a dropped connection actually created an issue. Not the primary duplicate guard — it cannot tell a tool-created test case from a human-linked one, which is why `state.json` holds that truth.

---

## Retry policy — FR-025

Applies to every call above, at the transport layer so no call site can forget it (research R3).

| Condition | Action |
|---|---|
| `429` | Honour `Retry-After` |
| `5xx` with `Retry-After` | Treat as rate limiting; honour the header |
| `5xx` without `Retry-After`, **reads** | Exponential backoff with jitter: `1s × 2^attempt` + jitter |
| `5xx` without `Retry-After`, **writes** | Do not retry — the write may have succeeded (SC-007) |
| Connection error, **reads** | Retry per backoff |
| Connection error, **writes** | Do not retry; reconcile |
| Max attempts | 5 total (1 + 4 retries). Then exit `12`, stating that retrying was abandoned |

The read/write asymmetry is the important part. Retrying a failed read is free; retrying a write whose outcome is unknown is how duplicates get created, and SC-007 forbids duplicates unconditionally.

## Request hygiene

- Explicit connect and read timeouts on every call; no unbounded waits
- One `httpx.Client` per run for connection reuse
- Writes are issued **sequentially**. Parallelising 25 creates would invite rate limiting and make partial-failure state harder to reason about, for a saving measured in seconds
- `Authorization` is redacted in every log record and every exception path (FR-027)
