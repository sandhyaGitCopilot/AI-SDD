# Contract: Test Case Generation

**Feature**: [spec.md](../spec.md) | **Research**: [research.md](../research.md) R7

How the requirement text becomes structured test cases. This is the step where requirement content leaves the user's machine — a deliberate, recorded decision (spec Assumptions, FR-029).

## Service

| Property | Value |
|---|---|
| Provider | Anthropic Claude API, official `anthropic` Python SDK |
| Model | `claude-opus-5-5` |
| Thinking | `{"type": "adaptive"}` |
| Effort | `output_config: {"effort": "high"}` — set **explicitly**, because this model defaults to `medium` |
| Output | Structured outputs via `output_config.format` with the schema below |
| Streaming | Yes — a 25-case draft is a long output; non-streaming risks HTTP timeouts |
| Credentials | `ANTHROPIC_API_KEY` from the environment, never logged (FR-027) |

Effort is set explicitly because leaving it unset would silently under-resource the task. Deriving negative and edge cases from prose is reasoning work, and SC-003 and SC-005 are quality bars that a quieter default would miss.

## Request shape

- **System prompt** (`generation/prompts.py`): the tester role, the required structure, the demand for negative and edge coverage, the self-contained-case rule (FR-010), and the cap.
- **User content**: issue key, summary, description text, acceptance criteria text, and the enumerated criteria with their ids — so `traces_to` can reference them (FR-007).
- **Max cases**: stated in the prompt *and* enforced in code after parsing. A prompt instruction is a request; FR-011 requires a limit that holds.

## Output schema

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["test_cases", "coverage_notes"],
  "properties": {
    "test_cases": {
      "type": "array", "minItems": 1, "maxItems": 25,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["summary", "preconditions", "steps", "expected_results", "traces_to", "kind"],
        "properties": {
          "summary":          { "type": "string", "minLength": 1, "maxLength": 255 },
          "preconditions":    { "type": "string" },
          "steps":            { "type": "array", "minItems": 1,
                                "items": { "type": "string", "minLength": 1 } },
          "expected_results": { "type": "array", "minItems": 1,
                                "items": { "type": "string", "minLength": 1 } },
          "traces_to":        { "type": "array", "minItems": 1,
                                "items": { "type": "string" } },
          "kind":             { "enum": ["positive", "negative", "edge"] }
        }
      }
    },
    "coverage_notes": { "type": "array", "items": { "type": "string" } }
  }
}
```

`test_id` is **not** in the schema. Identifiers are assigned locally after parsing (FR-006a) — letting the model invent them would risk collisions and make the one field the duplicate guard depends on non-deterministic.

## Post-parse validation

The schema guarantees shape, not sense. Each of these failures is reported as unusable output (FR-031, exit `8`) with no partial draft written:

| # | Check | Why |
|---|---|---|
| G1 | At least one test case returned | An empty result is not a draft |
| G2 | `len(expected_results)` is `1` or `len(steps)` per case | Mismatched counts are unreviewable |
| G3 | No case has only whitespace steps | Schema-valid but semantically empty (FR-006) |
| G4 | Case count ≤ `--max-cases` | Enforced in code, not left to the prompt (FR-011) |
| G5 | Every `traces_to` entry is a known criterion id or `REQ` | Unknown ids become a V8 warning, not silent bad traceability |
| G6 | Each case re-validates against the `TestCase` model | One validation path for model output and user edits alike |

## Coverage and shortfall reporting

`generation/coverage.py` computes, after parsing:

- criteria with no covering case → a `coverage_note` (FR-009, SC-004)
- the negative/edge proportion → reported so SC-005's 30% floor is measurable
- cap reached → a note naming what was left uncovered (FR-011)
- unread ADF node types carried from ingestion → a note (FR-009)

These notes go into the CSV preamble, where the reviewer sees them before approving. A coverage gap that is disclosed is acceptable under SC-004; a silent one is not.

## Failure handling — FR-031

| Condition | Handling |
|---|---|
| Unreachable / connection error | Exit `8`, message distinguishing this from a Jira failure |
| `401` / `403` | Exit `8`, naming the generation service's credentials specifically |
| `429` | Retry with backoff per the SDK's policy, then exit `8` |
| Request exceeds model limits | Exit `8`, suggesting a narrower requirement. Content is **never** silently truncated |
| `stop_reason: "refusal"` | Exit `8`, reporting the refusal category |
| Output fails G1–G6 | Exit `8`, naming the check that failed |

Every one of these is a distinct message, separate from Jira errors — the spec treats "the generation service failed" and "Jira failed" as different user problems, and SC-008 requires each to say what to do next.

## Determinism and testing

No test calls the real API. `generation/engine.py` takes an injected client, and tests supply recorded responses: a valid 12-case result, a schema-valid-but-empty result, an over-cap result, a refusal, and a rate-limit sequence. This keeps the suite free, fast, and stable — a test whose outcome depends on a live model is a test that fails for reasons unrelated to the code.

## Cost note

One run is one request: a typical story's requirement text in, up to 25 structured test cases out. At `claude-opus-5-5` rates ($4 per MTok input, $20 per MTok output) a typical run costs a few cents. Cheaper models were considered and rejected as the default in research R7 — test case quality is the product, and SC-003 and SC-005 are the metrics that would pay for the saving. The model is configurable for anyone who wants to make that trade knowingly.
