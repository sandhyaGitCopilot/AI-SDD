"""T014: the retry policy from contracts/jira-api.md.

The asymmetry tests matter most. A write retried on an ambiguous failure is how duplicate
Jira issues get created, and SC-007 forbids duplicates unconditionally -- so "does not retry"
is as much a requirement as "does retry".
"""

from __future__ import annotations

import httpx
import pytest
import respx

from jira_testgen.config import JiraSettings, Secret
from jira_testgen.errors import AuthFailure, IssueForbidden, IssueNotFound, RetriesExhausted
from jira_testgen.jira.client import (
    MAX_ATTEMPTS,
    AmbiguousWrite,
    JiraClient,
    _backoff_delay,
    _parse_retry_after,
)

pytestmark = pytest.mark.unit

BASE = "https://example.atlassian.net"
API = f"{BASE}/rest/api/3"


@pytest.fixture
def settings() -> JiraSettings:
    return JiraSettings(base_url=BASE, email="tester@example.com", api_token=Secret("fake-token"))


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Capture backoff delays instead of actually sleeping, so tests stay fast."""
    slept: list[float] = []
    monkeypatch.setattr("jira_testgen.jira.client.time.sleep", lambda s: slept.append(s))
    return slept


class TestRetryAfterParsing:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("5", 5.0),
            ("0", 0.0),
            ("2.5", 2.5),
            (None, None),
            ("", None),
            ("soon", None),
            ("-1", None),
        ],
    )
    def test_parse(self, header: str | None, expected: float | None) -> None:
        assert _parse_retry_after(header) == expected

    def test_caps_absurd_values(self) -> None:
        assert _parse_retry_after("99999") == 60.0


class TestBackoff:
    def test_grows_exponentially_with_jitter(self) -> None:
        for attempt in range(4):
            delay = _backoff_delay(attempt)
            base = 1.0 * (2**attempt)
            assert base <= delay <= base + 1.0

    def test_is_capped(self) -> None:
        assert _backoff_delay(20) <= 61.0


class TestReadRetries:
    @respx.mock
    def test_honours_retry_after_on_429(
        self, settings: JiraSettings, no_sleep: list[float]
    ) -> None:
        route = respx.get(f"{API}/field").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "7"}),
                httpx.Response(200, json=[{"id": "f1"}]),
            ]
        )
        with JiraClient(settings) as client:
            result = client.get("/field")

        assert result.status_code == 200
        assert route.call_count == 2
        assert no_sleep == [7.0], "Retry-After must be honoured exactly, not replaced by backoff"

    @respx.mock
    def test_treats_5xx_with_retry_after_as_throttling(
        self, settings: JiraSettings, no_sleep: list[float]
    ) -> None:
        """Atlassian signals throttling as 429 *or* 5xx carrying Retry-After (research R3)."""
        respx.get(f"{API}/field").mock(
            side_effect=[
                httpx.Response(503, headers={"Retry-After": "3"}),
                httpx.Response(200, json=[]),
            ]
        )
        with JiraClient(settings) as client:
            assert client.get("/field").status_code == 200
        assert no_sleep == [3.0]

    @respx.mock
    def test_backs_off_on_bare_5xx(self, settings: JiraSettings, no_sleep: list[float]) -> None:
        respx.get(f"{API}/field").mock(
            side_effect=[httpx.Response(500), httpx.Response(200, json=[])]
        )
        with JiraClient(settings) as client:
            assert client.get("/field").status_code == 200
        assert len(no_sleep) == 1
        assert 1.0 <= no_sleep[0] <= 2.0

    @respx.mock
    def test_retries_connection_errors(self, settings: JiraSettings, no_sleep: list[float]) -> None:
        respx.get(f"{API}/field").mock(
            side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json=[])]
        )
        with JiraClient(settings) as client:
            assert client.get("/field").status_code == 200

    @respx.mock
    def test_exhaustion_raises_retries_exhausted(
        self, settings: JiraSettings, no_sleep: list[float]
    ) -> None:
        """FR-025: stopping must be reported clearly, not as a generic failure."""
        route = respx.get(f"{API}/field").mock(return_value=httpx.Response(429))

        with JiraClient(settings) as client, pytest.raises(RetriesExhausted) as exc:
            client.get("/field")

        assert route.call_count == MAX_ATTEMPTS
        assert len(no_sleep) == MAX_ATTEMPTS - 1
        assert "Gave up" in exc.value.message
        assert exc.value.exit_code == 12


class TestWriteAsymmetry:
    """The SC-007 guard: an ambiguous write is never retried."""

    @respx.mock
    def test_does_not_retry_bare_5xx_on_write(
        self, settings: JiraSettings, no_sleep: list[float]
    ) -> None:
        route = respx.post(f"{API}/issue").mock(return_value=httpx.Response(500))

        with JiraClient(settings) as client, pytest.raises(AmbiguousWrite) as exc:
            client.post("/issue", {"fields": {}})

        assert route.call_count == 1, "a bare 5xx on a write must be sent exactly once"
        assert no_sleep == []
        assert "may have been applied" in exc.value.remediation

    @respx.mock
    def test_does_not_retry_connection_error_on_write(
        self, settings: JiraSettings, no_sleep: list[float]
    ) -> None:
        route = respx.post(f"{API}/issue").mock(side_effect=httpx.ConnectError("dropped"))

        with JiraClient(settings) as client, pytest.raises(AmbiguousWrite):
            client.post("/issue", {"fields": {}})

        assert route.call_count == 1

    @respx.mock
    def test_does_not_retry_timeout_on_write(
        self, settings: JiraSettings, no_sleep: list[float]
    ) -> None:
        route = respx.post(f"{API}/issue").mock(side_effect=httpx.ReadTimeout("slow"))

        with JiraClient(settings) as client, pytest.raises(AmbiguousWrite):
            client.post("/issue", {"fields": {}})

        assert route.call_count == 1

    @respx.mock
    def test_does_retry_429_on_write(self, settings: JiraSettings, no_sleep: list[float]) -> None:
        """A 429 is unambiguous -- the request was rejected, not processed."""
        route = respx.post(f"{API}/issue").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "2"}),
                httpx.Response(201, json={"key": "QA-1"}),
            ]
        )
        with JiraClient(settings) as client:
            result = client.post("/issue", {"fields": {}})

        assert result.json_body == {"key": "QA-1"}
        assert route.call_count == 2
        assert no_sleep == [2.0]


class TestClientErrorMapping:
    """401, 403, and 404 must stay distinct -- SC-008."""

    @respx.mock
    @pytest.mark.parametrize(
        ("status", "expected", "code"),
        [(401, AuthFailure, 5), (403, IssueForbidden, 4), (404, IssueNotFound, 3)],
    )
    def test_maps_status_to_typed_error(
        self, settings: JiraSettings, status: int, expected: type, code: int
    ) -> None:
        respx.get(f"{API}/issue/PROJ-1").mock(return_value=httpx.Response(status))

        with JiraClient(settings) as client, pytest.raises(expected) as exc:
            client.get("/issue/PROJ-1")

        assert exc.value.exit_code == code
        assert exc.value.remediation, "every error must say what to do next"

    @respx.mock
    def test_404_message_mentions_both_causes(self, settings: JiraSettings) -> None:
        """Jira cannot distinguish 'absent' from 'invisible', so the message must not pretend to."""
        respx.get(f"{API}/issue/PROJ-1").mock(return_value=httpx.Response(404))

        with JiraClient(settings) as client, pytest.raises(IssueNotFound) as exc:
            client.get("/issue/PROJ-1")

        assert "does not exist" in exc.value.remediation
        assert "cannot see it" in exc.value.remediation

    @respx.mock
    def test_surfaces_jira_field_errors_on_400(self, settings: JiraSettings) -> None:
        respx.post(f"{API}/issue").mock(
            return_value=httpx.Response(
                400, json={"errorMessages": [], "errors": {"customfield_1": "Field required."}}
            )
        )
        with JiraClient(settings) as client, pytest.raises(Exception) as exc:
            client.post("/issue", {"fields": {}})

        assert "customfield_1" in str(exc.value)

    @respx.mock
    def test_no_token_in_error_text(self, settings: JiraSettings) -> None:
        """FR-027: a failure path must not surface the credential."""
        respx.get(f"{API}/field").mock(return_value=httpx.Response(401))

        with JiraClient(settings) as client, pytest.raises(AuthFailure) as exc:
            client.get("/field")

        assert "fake-token" not in exc.value.render()
