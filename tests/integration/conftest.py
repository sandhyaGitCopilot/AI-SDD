"""Shared Jira mocking for integration tests.

Every route the generate flow touches is registered here so individual tests override only
what they care about. Nothing in this file contacts a real service.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from jira_testgen.config import GenerationSettings

BASE = "https://example.atlassian.net"
API = f"{BASE}/rest/api/3"


def adf(*paragraphs: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in paragraphs
        ],
    }


DEFAULT_DESCRIPTION = adf(
    "Users must be able to reset their password using their registered email address.",
    "Acceptance Criteria",
    "- A reset link is emailed to a registered address",
    "- An unregistered address shows a generic notice",
    "- The reset link expires after 24 hours",
)


class StubGenerationClient:
    """Injected in place of the Anthropic client -- no network, no cost, no variance."""

    def __init__(self, payload: Any | str | Exception) -> None:
        self.payload = payload
        self.calls: list[dict[str, str]] = []

    def generate(self, *, system: str, user_content: str, settings: GenerationSettings) -> str:
        self.calls.append({"system": system, "user_content": user_content})
        if isinstance(self.payload, Exception):
            raise self.payload
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload)


def make_case(index: int = 1, kind: str = "positive", **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "summary": f"Test case {index}",
        "preconditions": "A registered account exists.",
        "steps": ["Open the reset page", "Enter an email", "Submit"],
        "expected_results": ["Page loads", "Field accepts input", "Notice appears"],
        "traces_to": ["AC-1"],
        "kind": kind,
    }
    data.update(overrides)
    return data


def default_payload(count: int = 4) -> dict[str, Any]:
    kinds = ["positive", "negative", "edge", "negative"]
    return {
        "test_cases": [
            make_case(i + 1, kinds[i % len(kinds)], traces_to=[f"AC-{(i % 3) + 1}"])
            for i in range(count)
        ],
        "coverage_notes": [],
    }


@pytest.fixture
def stub_generation() -> Callable[[Any], StubGenerationClient]:
    def factory(payload: Any) -> StubGenerationClient:
        return StubGenerationClient(payload)

    return factory


@pytest.fixture
def jira_mock(jira_env: dict[str, str]) -> Any:
    """All routes the generate flow needs, each overridable per test."""
    with respx.mock(base_url=API, assert_all_called=False) as mock:
        # Jira answers several read endpoints anonymously when a token is wrong, so the
        # tool probes /myself first -- the one endpoint that does honour auth (T073).
        mock.get("/myself").mock(
            return_value=httpx.Response(
                200,
                json={
                    "accountId": "5b10a2844c20165700ede21g",
                    "displayName": "Test Account",
                    "emailAddress": "tester@example.com",
                },
            )
        )
        mock.get("/field").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": "summary", "name": "Summary", "custom": False},
                    {"id": "customfield_10001", "name": "Acceptance Criteria", "custom": True},
                    {"id": "customfield_10002", "name": "Story Points", "custom": True},
                ],
            )
        )
        mock.get("/issueLinkType").mock(
            return_value=httpx.Response(
                200,
                json={
                    "issueLinkTypes": [
                        {
                            "id": "10000",
                            "name": "Relates",
                            "inward": "relates to",
                            "outward": "relates to",
                        },
                        {
                            "id": "10001",
                            "name": "Blocks",
                            "inward": "is blocked by",
                            "outward": "blocks",
                        },
                    ]
                },
            )
        )
        mock.get("/issue/createmeta").mock(
            return_value=httpx.Response(
                200,
                json={
                    "projects": [
                        {
                            "key": "PROJ",
                            "issuetypes": [
                                {"id": "10001", "name": "Task"},
                                {"id": "10002", "name": "Bug"},
                                {"id": "10003", "name": "Test"},
                            ],
                        }
                    ]
                },
            )
        )
        mock.get("/mypermissions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "permissions": {
                        "CREATE_ISSUES": {"havePermission": True},
                        "LINK_ISSUES": {"havePermission": True},
                    }
                },
            )
        )
        mock.get(path__regex=r"/issue/[A-Z][A-Z0-9_]+-\d+$").mock(
            return_value=httpx.Response(
                200,
                json={
                    "key": "PROJ-123",
                    "fields": {
                        "summary": "Users can reset their password by email",
                        "description": DEFAULT_DESCRIPTION,
                        "project": {"key": "PROJ"},
                        "issuetype": {"name": "Story"},
                        "issuelinks": [],
                    },
                },
            )
        )
        yield mock


# ---------------------------------------------------------------------------
# Fixture drafts (User Story 2 / User Story 3)
#
# US2 and US3 are specified as independently testable *from a fixture draft* --
# they must not need the generate flow to have run. Everything below builds a
# run directory (state.json + testcases.csv) directly, so review, validation,
# approval, and publishing can be exercised with no Jira read and no model call.
# ---------------------------------------------------------------------------

from dataclasses import dataclass  # noqa: E402
from pathlib import Path  # noqa: E402

from jira_testgen.draft.state import DRAFT_FILENAME, RunStateStore, run_dir  # noqa: E402
from jira_testgen.models import (  # noqa: E402
    AcceptanceCriterion,
    PublicationEntry,
    RunPhase,
    RunState,
    SourceRequirement,
)

DRAFT_COLUMNS = [
    "test_id",
    "approval",
    "kind",
    "summary",
    "preconditions",
    "steps",
    "expected_results",
    "traces_to",
    "jira_key",
    "notes",
]

DEFAULT_PREAMBLE = [
    "jira-testgen draft v1",
    "run_id: PROJ-123-20261006-142233",
    "source_issue: PROJ-123",
    "source_summary: Users can reset their password by email",
    "fields_read: summary, description, Acceptance Criteria",
    "generated_by: anthropic/claude-opus-5-5",
    "generated_at: 2026-10-06T14:22:33+00:00",
    "DO NOT EDIT the test_id column. Edit any other cell freely.",
]

RUN_SEQ = "AB12"


def make_row(index: int = 1, **overrides: Any) -> dict[str, str]:
    """One well-formed draft row. Override any cell to build a malformed one."""
    row = {
        "test_id": f"TC-{RUN_SEQ}-{index:03d}",
        "approval": "approved",
        "kind": "positive",
        "summary": f"Verify password reset behaviour {index}",
        "preconditions": "A registered account exists.",
        "steps": "1. Open the password reset page\n2. Submit the form",
        "expected_results": "1. The page loads\n2. A confirmation notice appears",
        "traces_to": "AC-1",
        "jira_key": "",
        "notes": "",
    }
    row.update({k: str(v) for k, v in overrides.items()})
    return row


def render_csv(
    rows: list[dict[str, str]],
    *,
    preamble: list[str] | None = DEFAULT_PREAMBLE,
    columns: list[str] | None = None,
    delimiter: str = ",",
    terminator: str = "\r\n",
    encoding: str = "utf-8-sig",
) -> bytes:
    """Render draft rows to bytes, so a test can vary encoding and delimiter exactly."""
    import csv as csv_mod
    import io as io_mod

    buffer = io_mod.StringIO(newline="")
    for line in preamble or []:
        buffer.write(f"# {line}{terminator}")
    writer = csv_mod.DictWriter(
        buffer,
        fieldnames=columns or DRAFT_COLUMNS,
        delimiter=delimiter,
        lineterminator=terminator,
        quoting=csv_mod.QUOTE_MINIMAL,
        extrasaction="ignore",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode(encoding)


def fixture_source() -> SourceRequirement:
    return SourceRequirement(
        issue_key="PROJ-123",
        summary="Users can reset their password by email",
        description_text="Users must be able to reset their password using their email.",
        acceptance_criteria_text="- A reset link is emailed\n- Unregistered shows a notice",
        criteria=[
            AcceptanceCriterion(criterion_id="AC-1", text="A reset link is emailed"),
            AcceptanceCriterion(criterion_id="AC-2", text="Unregistered shows a notice"),
            AcceptanceCriterion(criterion_id="AC-3", text="The link expires after 24 hours"),
        ],
        fields_read=["summary", "description", "Acceptance Criteria"],
        project_key="PROJ",
    )


@dataclass
class RunFixture:
    """A run directory on disk, ready for `approve` to act on."""

    workspace: Path
    run_id: str
    directory: Path
    draft_path: Path
    state: RunState


@pytest.fixture
def make_run(tmp_path: Path) -> Callable[..., RunFixture]:
    """Build a run directory from fixture content -- no generation, no Jira read."""

    def factory(
        rows: list[dict[str, str]] | None = None,
        *,
        run_id: str = "PROJ-123-20261006-142233",
        phase: RunPhase = RunPhase.DRAFTED,
        published: dict[str, str] | None = None,
        linked: bool = True,
        raw_csv: bytes | None = None,
        **csv_kwargs: Any,
    ) -> RunFixture:
        workspace = tmp_path / ".jira-testgen"
        directory = run_dir(workspace, run_id)
        directory.mkdir(parents=True, exist_ok=True)

        draft_path = directory / DRAFT_FILENAME
        if raw_csv is not None:
            draft_path.write_bytes(raw_csv)
        else:
            body = rows if rows is not None else [make_row(i) for i in range(1, 4)]
            draft_path.write_bytes(render_csv(body, **csv_kwargs))

        record = {
            test_id: PublicationEntry(
                test_id=test_id,
                issue_key=key,
                issue_url=f"{BASE}/browse/{key}",
                linked=linked,
            )
            for test_id, key in (published or {}).items()
        }

        state = RunState(
            run_id=run_id,
            phase=phase,
            source_snapshot=fixture_source(),
            draft_path=str(draft_path),
            target_project_key="PROJ",
            issue_type_id="10003",
            issue_type_name="Test",
            link_type_name="Relates",
            publication_record=record,
            generation_meta={"service": "anthropic/claude-opus-5-5"},
        )
        RunStateStore(directory).save(state)
        return RunFixture(workspace, run_id, directory, draft_path, state)

    return factory


class StubPublisher:
    """Stands in for the User Story 3 writer so US2 can be tested with no Jira writes."""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.published: list[str] = []
        self.seen_summaries: list[str] = []
        self.fail_on = fail_on or set()
        self.calls = 0

    def publish(self, *, run, cases, state, store, console=None):  # type: ignore[no-untyped-def]
        from jira_testgen.commands.approve import PublishOutcome

        self.calls += 1
        self.seen_summaries.extend(case.summary for case in cases)
        created: list[dict[str, str]] = []
        failures: list[dict[str, str]] = []
        for case in cases:
            if case.test_id in self.fail_on:
                failures.append({"test_id": case.test_id, "error": "stubbed failure"})
                continue
            key = f"PROJ-{900 + len(self.published)}"
            self.published.append(case.test_id)
            store.record_issue_created(state, case.test_id, key, f"{BASE}/browse/{key}")
            store.record_linked(state, case.test_id)
            created.append(
                {"test_id": case.test_id, "issue_key": key, "issue_url": f"{BASE}/browse/{key}"}
            )
        return PublishOutcome(created=created, failures=failures, repaired=[])


@pytest.fixture
def stub_publisher() -> Callable[..., StubPublisher]:
    def factory(fail_on: set[str] | None = None) -> StubPublisher:
        return StubPublisher(fail_on)

    return factory


# ---------------------------------------------------------------------------
# Jira write mocking (User Story 3)
#
# Separate from `jira_mock` on purpose. US1 and US2 assert that *zero* writes
# happen, and a fixture that quietly registered write routes for them would make
# that assertion weaker without anyone noticing.
# ---------------------------------------------------------------------------

import itertools  # noqa: E402


class JiraWrites:
    """Records every create and link, and lets a test make either one fail."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.links: list[dict[str, Any]] = []
        self.create_attempts = 0
        self.link_attempts = 0
        #: Called with the parsed request body; return an httpx.Response to override the
        #: default 201, or raise to simulate the process dying mid-run.
        self.on_create: Any = None
        self.on_link: Any = None
        self._keys = itertools.count(900)

    @property
    def created_keys(self) -> list[str]:
        return [item["key"] for item in self.created]

    @property
    def created_summaries(self) -> list[str]:
        return [item["summary"] for item in self.created]

    @property
    def linked_keys(self) -> list[str]:
        return [item["inward"] for item in self.links]


@pytest.fixture
def jira_writes(jira_mock: Any) -> Any:
    """Register create, link, and reconciliation-search routes on the Jira mock."""
    import json as json_mod

    recorder = JiraWrites()

    def create(request: httpx.Request) -> httpx.Response:
        recorder.create_attempts += 1
        body = json_mod.loads(request.content)
        if recorder.on_create is not None:
            override = recorder.on_create(body, recorder)
            if override is not None:
                return override
        key = f"QA-{next(recorder._keys)}"
        recorder.created.append({"key": key, "summary": body["fields"]["summary"], "body": body})
        return httpx.Response(
            201, json={"id": key.split("-")[1], "key": key, "self": f"{API}/issue/{key}"}
        )

    def link(request: httpx.Request) -> httpx.Response:
        recorder.link_attempts += 1
        body = json_mod.loads(request.content)
        if recorder.on_link is not None:
            override = recorder.on_link(body, recorder)
            if override is not None:
                return override
        recorder.links.append(
            {
                "type": body["type"]["name"],
                "inward": body["inwardIssue"]["key"],
                "outward": body["outwardIssue"]["key"],
            }
        )
        return httpx.Response(201)

    def search(request: httpx.Request) -> httpx.Response:
        """Reconciliation search -- returns whatever has actually been created."""
        return httpx.Response(
            200,
            json={
                "issues": [
                    {
                        "key": item["key"],
                        "fields": {"summary": item["summary"], "issuetype": {"name": "Test"}},
                    }
                    for item in recorder.created
                ]
            },
        )

    jira_mock.post("/issue").mock(side_effect=create)
    jira_mock.post("/issueLink").mock(side_effect=link)
    jira_mock.get(path__startswith="/search/jql").mock(side_effect=search)
    return recorder


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Capture retry delays rather than sleeping through them."""
    slept: list[float] = []
    monkeypatch.setattr("jira_testgen.jira.client.time.sleep", lambda s: slept.append(s))
    return slept
