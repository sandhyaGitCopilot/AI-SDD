# Adoption metrics

**Feature**: [Jira Test Case Generator](../specs/001-jira-test-case-generator/spec.md)
**Created**: 2026-10-07 · **Status**: awaiting first-week data

Five of the spec's success criteria cannot be asserted by any command. They are measured
over a sample of real use, by people, against their own work. This file is where that
measurement is recorded.

It exists because the alternative is worse. A criterion with no home quietly becomes a
criterion nobody checks, and then the feature is "done" on the strength of the criteria
that happened to be easy to automate — which are not the ones that say whether it is any
good.

> **Everything below is a blank record, not a result.** No figures have been collected.
> Do not cite this file as evidence that any criterion is met.

---

## How to collect

Run the tool as you normally would, on real requirements, for one week. Do not use
scratch issues for this — the point is to measure the tool against work someone actually
cares about. Record each run as a row in the tables below, then fill in the verdict.

A sample of **at least five requirements across at least two people** is the minimum
worth drawing a conclusion from. Fewer than that and the honest verdict is "not enough
data", which is a legitimate thing to write here.

---

## SC-001 — A QA engineer produces a reviewable draft without reading the docs

**Criterion**: a first-time user gets from a Jira issue key to a draft they would be
willing to review, without being walked through it.

**How to measure**: sit with someone who has not used the tool. Give them the issue key
and nothing else. Do not answer questions unless they are stuck for more than two
minutes; record the question instead.

| Date | Person (role) | Issue | Reached a draft? | Questions they had to ask | Notes |
|---|---|---|---|---|---|
| | | | | | |

**Verdict**: _not yet measured_

---

## SC-002 — Time from issue key to reviewable draft

**Criterion**: the draft arrives fast enough that the user waits for it rather than
switching tasks.

**How to measure**: wall-clock from pressing enter on `generate` to the draft table
appearing. The run log in `.jira-testgen/runs/<run-id>/run.log` has timestamped entries
if you would rather read it off than hold a stopwatch.

| Date | Issue | Cases generated | Seconds to draft | Did they wait or switch away? |
|---|---|---|---|---|
| | | | | |

**Verdict**: _not yet measured_

---

## SC-003 — Edit rate

**Criterion**: the generated cases are good enough that reviewers mostly keep them.

**How to measure**: per run, count rows the reviewer changed in any way (summary, steps,
expected results, preconditions), rows they rejected, and rows they added by hand. A
hand-added row is the most informative of the three: it is the reviewer telling you what
the generator missed.

| Date | Issue | Cases | Edited | Rejected | Added by hand | Edit rate |
|---|---|---|---|---|---|---|
| | | | | | | |

**Verdict**: _not yet measured_

**Watch for**: a *low* edit rate is not automatically good. A reviewer who approves
everything without reading has produced a 0% edit rate and no value. Cross-check against
SC-011 — a 25-case draft approved in ninety seconds was not reviewed.

---

## SC-009 — Tester comprehension

**Criterion**: a tester who did not write the requirement can execute a published test
case without asking the author what it means. This is what FR-010's self-contained-case
rule is for.

**How to measure**: hand published test case issues to a tester who was not involved.
Ask them to execute them. Count how many they could run as written.

| Date | Tester | Cases given | Executed without asking | What they had to ask about |
|---|---|---|---|---|
| | | | | |

**Verdict**: _not yet measured_

---

## SC-011 — Review time at full size

**Criterion**: a draft at the 25-case cap is still reviewable in one sitting. This is the
criterion the cap exists to protect, so if it fails the cap is wrong, not the reviewer.

**How to measure**: time a review of a draft at or near 25 cases, start to approval.
Record interruptions separately — elapsed time is not attention.

| Date | Issue | Cases | Minutes reviewing | Interruptions | Finished in one sitting? |
|---|---|---|---|---|---|
| | | | | | |

**Verdict**: _not yet measured_

---

## What the automated suite already covers

So nobody re-measures these by hand. Each is asserted on every test run:

| Criterion | Asserted by |
|---|---|
| SC-004 coverage shortfall is disclosed | `generation/coverage.py`, `tests/integration/test_generate_flow.py` |
| SC-005 negative/edge proportion | `tests/integration/test_generate_flow.py` |
| SC-006 nothing created without approval | `test_generate_flow.py`, `test_reject_flow.py`, `test_generate_review_gate.py` |
| SC-007 no duplicates, ever | `tests/integration/test_partial_publish_retry.py`, `test_republish_guards.py` |
| SC-008 every error says what to do | `tests/unit/test_exit_codes.py` |
| SC-010 resume without regenerating | `tests/integration/test_resume_after_interrupt.py` |
| SC-012 spreadsheet round trip | `tests/integration/test_spreadsheet_roundtrip.py` |

---

## Decision point

Once the sample is in, write the outcome here — including "we are not going to adopt
this", if that is what the numbers say.

**Date reviewed**: _pending_
**Outcome**: _pending_
