"""Publishing approved test cases as linked Jira issues (FR-019 to FR-024, research R4).

Everything in this module is shaped by one constraint: Jira's create-issue endpoint takes
no client-supplied idempotency key, so the server cannot tell us whether a request we are
unsure about already happened. SC-007 forbids duplicates unconditionally. The defence is
entirely local, and it has four parts:

1. **Record on 201, before anything else.** The issue key goes to disk the instant the
   create succeeds -- before the link is attempted and before moving to the next case.
   Batching the writes would widen the damage from a crash from one ambiguous case to all
   twenty-five.

2. **Create and link are recorded apart.** They are two calls that fail independently. A
   crash between them leaves a real issue with no link, which is only repairable if the
   two facts are tracked separately.

3. **Repair before create.** The repair pass runs first. An orphaned issue is the more
   urgent problem, and leaving it until after a batch of new creates widens the window in
   which a second interruption strands it again.

4. **Never blindly retry an ambiguous write.** A dropped connection or a bare 5xx on a
   POST may mean the issue was created and the response was lost. The client raises
   ``AmbiguousWrite`` rather than retrying, and this module reconciles against Jira
   before deciding anything.

Writes are sequential by design. Parallelising twenty-five creates would invite rate
limiting and make partial-failure state much harder to reason about, for a saving measured
in seconds.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any

from rich.console import Console

from jira_testgen.config import Settings, load_settings, redact
from jira_testgen.errors import MissingProjectPermission, PublishFailure
from jira_testgen.jira.adf import build_test_case_adf
from jira_testgen.jira.client import AmbiguousWrite, JiraBadRequest, JiraClient
from jira_testgen.models import RunState, TestCase

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to the type checker
    from jira_testgen.commands.approve import PublishOutcome
    from jira_testgen.draft.state import RunStateStore, RunSummary

logger = logging.getLogger("jira_testgen.jira.writer")

#: Fields asked for when reconciling an ambiguous write (contracts/jira-api.md).
RECONCILE_FIELDS = "summary,issuetype"


class JiraPublisher:
    """Creates and links the issues for one approved draft.

    A client may be injected; otherwise one is built from the environment and owned for
    the duration of ``publish``. Settings are resolved with ``require_generation=False``
    -- demanding an Anthropic key in order to publish a draft that already exists would
    be absurd, and would break the resume path for anyone who rotated it.
    """

    def __init__(self, client: JiraClient | None = None, settings: Settings | None = None) -> None:
        self._client = client
        self._settings = settings

    def publish(
        self,
        *,
        run: RunSummary,
        cases: list[TestCase],
        state: RunState,
        store: RunStateStore,
        console: Console | None = None,
    ) -> PublishOutcome:
        from jira_testgen.commands.approve import PublishOutcome

        settings = self._settings or load_settings(require_generation=False)
        outcome = PublishOutcome()

        with ExitStack() as stack:
            client = self._client or stack.enter_context(JiraClient(settings.jira))
            self._repair_links(client, state, store, outcome, settings, console)
            self._create_missing(client, cases, state, store, outcome, settings, console)

        return outcome

    # -- step 1: repair -------------------------------------------------

    def _repair_links(
        self,
        client: JiraClient,
        state: RunState,
        store: RunStateStore,
        outcome: PublishOutcome,
        settings: Settings,
        console: Console | None,
    ) -> None:
        """Link issues that exist but were never linked (FR-020, FR-023).

        Runs before any create, so an orphan from a previous interruption is fixed at the
        first opportunity rather than after another batch of work.
        """
        pending = state.entries_needing_link()
        if not pending:
            return

        if console:
            console.print(
                f"[yellow]Repairing {len(pending)} issue(s) created earlier but not "
                f"linked.[/yellow]"
            )

        for entry in pending:
            if entry.issue_key is None:  # pragma: no cover - guarded by needs_link_repair
                continue
            self._link(client, state, entry.issue_key)
            store.record_linked(state, entry.test_id)
            outcome.repaired.append(entry.test_id)

    # -- step 2: create and link ---------------------------------------

    def _create_missing(
        self,
        client: JiraClient,
        cases: list[TestCase],
        state: RunState,
        store: RunStateStore,
        outcome: PublishOutcome,
        settings: Settings,
        console: Console | None,
    ) -> None:
        for case in cases:
            entry = state.publication_record.get(case.test_id)
            if entry is not None and entry.is_published:
                # FR-023. Identity is the identifier, never the summary text -- a
                # reviewer who fixes a typo after publishing must not get a second issue.
                logger.info("Skipping %s; already filed as %s", case.test_id, entry.issue_key)
                continue

            try:
                issue_key = self._create_one(client, case, state, store, settings)
            except AmbiguousWrite as exc:
                recovered = self._reconcile(client, case, state, store, settings, exc, console)
                if recovered is None:
                    outcome.failures.append({"test_id": case.test_id, "error": exc.render()})
                    continue
                issue_key = recovered

            try:
                self._link(client, state, issue_key)
            except AmbiguousWrite as exc:
                # The issue exists and is recorded; only the link is in doubt. Leaving it
                # unlinked is a state the repair pass already knows how to finish.
                store.record_failure(state, case.test_id, redact(str(exc), settings.secrets()))
                outcome.failures.append({"test_id": case.test_id, "error": exc.render()})
                continue

            store.record_linked(state, case.test_id)
            outcome.created.append(
                {
                    "test_id": case.test_id,
                    "issue_key": issue_key,
                    "issue_url": settings.jira.browse_url(issue_key),
                }
            )
            if console:
                console.print(f"  [green]+[/green] {case.test_id} -> {issue_key}")

    def _create_one(
        self,
        client: JiraClient,
        case: TestCase,
        state: RunState,
        store: RunStateStore,
        settings: Settings,
    ) -> str:
        """POST the issue and record the key immediately on 201 (research R4)."""
        payload = self._issue_payload(case, state)

        try:
            response = client.post("/issue", payload)
        except JiraBadRequest as exc:
            # Deterministic: every remaining case has the same shape and would fail
            # identically. Stop and name the field rather than emit 25 identical errors.
            raise self._field_rejection(case, state, exc) from exc
        except Exception as exc:
            if self._is_forbidden(exc):
                raise self._permission_lost(state) from exc
            store.record_failure(state, case.test_id, redact(str(exc), settings.secrets()))
            raise

        issue_key = str((response.json_body or {}).get("key") or "")
        if not issue_key:  # pragma: no cover - Jira always returns a key with 201
            raise PublishFailure(
                f"Jira accepted the issue for {case.test_id} but returned no issue key.",
                "Check the target project in Jira for an issue matching the summary, then "
                "re-run; already-recorded cases are skipped.",
            )

        # The load-bearing line of the whole feature: persisted synchronously, before the
        # link is attempted and before the next case is touched.
        store.record_issue_created(
            state, case.test_id, issue_key, settings.jira.browse_url(issue_key)
        )
        return issue_key

    def _link(self, client: JiraClient, state: RunState, issue_key: str) -> None:
        """POST the link. Recorded by the caller, separately from creation (FR-020)."""
        try:
            client.post(
                "/issueLink",
                {
                    "type": {"name": state.link_type_name},
                    "inwardIssue": {"key": issue_key},
                    "outwardIssue": {"key": state.source_snapshot.issue_key},
                },
            )
        except AmbiguousWrite:
            raise
        except JiraBadRequest as exc:
            raise PublishFailure(
                f"Jira rejected the link between {issue_key} and "
                f"{state.source_snapshot.issue_key}: {exc.detail}",
                f"Check that the link type {state.link_type_name!r} still exists on this "
                f"site. {issue_key} was created and is recorded; re-running will link it "
                "rather than create it again.",
            ) from exc
        except Exception as exc:
            if self._is_forbidden(exc):
                raise self._permission_lost(state) from exc
            raise

    # -- the ambiguous write (T062) -------------------------------------

    def _reconcile(
        self,
        client: JiraClient,
        case: TestCase,
        state: RunState,
        store: RunStateStore,
        settings: Settings,
        cause: AmbiguousWrite,
        console: Console | None,
    ) -> str | None:
        """Decide whether an ambiguous create actually created something.

        Never retries. Asks Jira what exists and matches on summary, which is good enough
        *here* -- the tool wrote that summary seconds ago and is choosing between "my own
        write landed" and "it did not", not trying to identify arbitrary issues.

        Returns the adopted issue key, or ``None`` when nothing matching was found. It
        deliberately does not create the issue in the not-found case: Jira's search is
        index-backed and can lag a create by seconds, so "not found" is weaker evidence
        than it looks, and acting on it is exactly how a duplicate gets filed.
        """
        logger.warning("Ambiguous write for %s: %s", case.test_id, cause.message)

        found = self._find_matching_issue(client, case, state)

        if found is not None:
            store.record_issue_created(state, case.test_id, found, settings.jira.browse_url(found))
            if console:
                console.print(
                    f"  [yellow]~[/yellow] {case.test_id}: the connection dropped, but "
                    f"{found} was found in Jira and has been adopted rather than filed again."
                )
            return found

        if console:
            console.print(
                f"  [red]?[/red] {case.test_id}: the write may or may not have reached "
                "Jira, and no matching issue was found. It was NOT retried, because "
                "retrying an unknown write is how duplicates are created.\n"
                f"      Check {state.target_project_key} for an issue titled "
                f"{case.summary!r} before re-running."
            )
        return None

    def _find_matching_issue(
        self, client: JiraClient, case: TestCase, state: RunState
    ) -> str | None:
        """Look for an issue this run may already have filed.

        Two queries, because one is not enough. contracts/jira-api.md specifies the
        ``linkedIssues`` search, and that is the right query when the *link* was the
        ambiguous call. But when the ambiguous call was the **create**, the issue cannot
        be linked yet by construction, so that query can never match it. The second
        query covers that case by searching the target project directly.
        """
        source_key = state.source_snapshot.issue_key
        queries = [
            f'issue in linkedIssues("{source_key}")',
            f'project = "{state.target_project_key}" AND summary ~ '
            f'"{self._escape_jql(case.summary)}"',
        ]

        for jql in queries:
            try:
                response = client.get(
                    "/search/jql", params={"jql": jql, "fields": RECONCILE_FIELDS}
                )
            except Exception:
                logger.warning("Reconciliation query failed: %s", jql, exc_info=True)
                continue

            for issue in (response.json_body or {}).get("issues") or []:
                summary = str((issue.get("fields") or {}).get("summary") or "")
                if summary.strip() == case.summary.strip():
                    return str(issue.get("key") or "") or None

        return None

    @staticmethod
    def _escape_jql(value: str) -> str:
        """Escape a value for embedding in a double-quoted JQL string literal."""
        return value.replace("\\", "\\\\").replace('"', '\\"')

    # -- payload and errors ---------------------------------------------

    @staticmethod
    def _issue_payload(case: TestCase, state: RunState) -> dict[str, Any]:
        """Build the create body. ``description`` must be ADF -- a string is a 400 (R1).

        The ``notes`` column is deliberately absent: contracts/draft-csv.md reserves it
        for the reviewer, and publishing it would leak internal review chatter into a
        shared project.
        """
        return {
            "fields": {
                "project": {"key": state.target_project_key},
                "issuetype": {"id": state.issue_type_id},
                "summary": case.summary,
                "description": build_test_case_adf(
                    preconditions=case.preconditions,
                    steps=case.steps,
                    expected_results=case.expected_results,
                    traces_to=case.traces_to,
                    source_issue_key=state.source_snapshot.issue_key,
                ),
            }
        }

    @staticmethod
    def _is_forbidden(exc: Exception) -> bool:
        from jira_testgen.errors import IssueForbidden

        return isinstance(exc, IssueForbidden)

    @staticmethod
    def _permission_lost(state: RunState) -> MissingProjectPermission:
        """A 403 while publishing is a project permission, not an unreadable issue.

        Mapping it to exit 4 would tell the user their source issue is unreadable, which
        is both wrong and unactionable -- they just read it.
        """
        return MissingProjectPermission(
            f"Jira refused a write to {state.target_project_key} (HTTP 403) partway "
            "through publishing.",
            f"Your account no longer has CREATE_ISSUES or LINK_ISSUES on "
            f"{state.target_project_key}. Ask a Jira administrator to restore it, then "
            "re-run; issues already created are recorded and will not be filed twice.",
        )

    @staticmethod
    def _field_rejection(case: TestCase, state: RunState, exc: JiraBadRequest) -> PublishFailure:
        named = ", ".join(exc.field_errors) or "(no field named)"
        return PublishFailure(
            f"Jira rejected the issue for {case.test_id}: {exc.detail}",
            f"The target project {state.target_project_key} requires or disallows "
            f"field(s) this tool does not set: {named}. This is deterministic, so the "
            "run stopped rather than failing every remaining case the same way. Adjust "
            "the project's field configuration, or publish to a project whose test case "
            "issue type has no extra mandatory fields.",
        )
