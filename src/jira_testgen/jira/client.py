"""Jira HTTP client with the retry policy from contracts/jira-api.md.

The read/write asymmetry here is the point of the module, and it is load-bearing for SC-007.

Retrying a failed *read* is free. Retrying a *write* whose outcome is unknown is precisely how
duplicate issues get created: a 500 or a dropped connection on ``POST /issue`` may mean the
issue was created and the response was lost. So writes retry only on signals that are
unambiguous about the request not having been processed -- a 429, or a 5xx that carries
``Retry-After`` (which is Atlassian telling us it is throttling, not failing).

Everything else on a write raises ``AmbiguousWrite``, and the caller reconciles (research R4).
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from jira_testgen.config import JiraSettings
from jira_testgen.errors import (
    AuthFailure,
    IssueForbidden,
    IssueNotFound,
    JiraTestGenError,
    RetriesExhausted,
)

logger = logging.getLogger("jira_testgen.jira.client")

MAX_ATTEMPTS = 5  # 1 initial + 4 retries (research R3)
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 30.0

Method = Literal["GET", "POST", "PUT", "DELETE"]
_WRITE_METHODS = frozenset({"POST", "PUT", "DELETE"})


class AmbiguousWrite(JiraTestGenError):
    """A write was sent but its outcome is unknown.

    Not retried automatically: the request may have succeeded. The caller reconciles against
    Jira before deciding (research R4).
    """

    exit_code = 11

    def __init__(self, message: str, remediation: str, method: str = "", path: str = "") -> None:
        super().__init__(message, remediation)
        self.method = method
        self.path = path


class JiraBadRequest(JiraTestGenError):
    """Jira rejected the request body itself (HTTP 400).

    Typed separately because it is the one 4xx that is *deterministic*: the payload is
    wrong for this site's configuration, so retrying cannot help and neither can trying
    the next case. The caller needs the field names to tell the user what to fix
    (contracts/jira-api.md, SC-008).
    """

    exit_code = 11

    def __init__(
        self,
        message: str,
        remediation: str,
        detail: str = "",
        field_errors: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message, remediation)
        self.detail = detail
        self.field_errors = field_errors or {}


@dataclass(frozen=True)
class JiraResponse:
    status_code: int
    json_body: Any
    headers: dict[str, str]


def _parse_retry_after(value: str | None) -> float | None:
    """Parse ``Retry-After``. Only the delay-seconds form is honoured."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, MAX_BACKOFF_SECONDS)


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter: 1s * 2^attempt, plus up to 1s (research R3)."""
    base: float = min(BASE_BACKOFF_SECONDS * (2**attempt), MAX_BACKOFF_SECONDS)
    jitter: float = random.uniform(0, 1.0)
    return base + jitter


class JiraClient:
    """A thin, retrying wrapper over one ``httpx.Client``.

    Retry lives here rather than at each call site so no future caller can forget it.
    """

    def __init__(self, settings: JiraSettings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=settings.api_root,
            auth=(settings.email, settings.api_token.reveal()),
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    def __enter__(self) -> JiraClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- public surface -------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> JiraResponse:
        return self._request("GET", path, params=params)

    def post(self, path: str, json_body: dict[str, Any]) -> JiraResponse:
        return self._request("POST", path, json_body=json_body)

    # -- internals ------------------------------------------------------

    def _request(
        self,
        method: Method,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> JiraResponse:
        is_write = method in _WRITE_METHODS
        last_detail = ""

        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._client.request(method, path, params=params, json=json_body)
            except httpx.TimeoutException as exc:
                last_detail = f"timeout: {exc}"
                if is_write:
                    raise AmbiguousWrite(
                        f"The request {method} {path} timed out after being sent.",
                        "It may have succeeded. Re-run the command: already-created items are "
                        "recorded and will be reconciled rather than duplicated.",
                        method=method,
                        path=path,
                    ) from exc
                if attempt == MAX_ATTEMPTS - 1:
                    break
                self._sleep(_backoff_delay(attempt), reason="timeout")
                continue
            except httpx.TransportError as exc:
                last_detail = f"connection error: {exc}"
                if is_write:
                    raise AmbiguousWrite(
                        f"The connection dropped during {method} {path}.",
                        "The write may have succeeded. Re-run the command so it can reconcile "
                        "against Jira instead of creating a duplicate.",
                        method=method,
                        path=path,
                    ) from exc
                if attempt == MAX_ATTEMPTS - 1:
                    break
                self._sleep(_backoff_delay(attempt), reason="connection error")
                continue

            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            throttled = response.status_code == 429 or (
                response.status_code >= 500 and retry_after is not None
            )

            if throttled:
                last_detail = f"HTTP {response.status_code} (throttled)"
                if attempt == MAX_ATTEMPTS - 1:
                    break
                delay = retry_after if retry_after is not None else _backoff_delay(attempt)
                self._sleep(delay, reason=f"HTTP {response.status_code}")
                continue

            if response.status_code >= 500:
                last_detail = f"HTTP {response.status_code}"
                # A bare 5xx on a write is ambiguous -- never retry it (SC-007).
                if is_write:
                    raise AmbiguousWrite(
                        f"Jira returned HTTP {response.status_code} for {method} {path}.",
                        "The write may have been applied. Re-run the command so it can "
                        "reconcile rather than risk creating a duplicate.",
                        method=method,
                        path=path,
                    )
                if attempt == MAX_ATTEMPTS - 1:
                    break
                self._sleep(_backoff_delay(attempt), reason=f"HTTP {response.status_code}")
                continue

            self._raise_for_client_error(response, path)
            return JiraResponse(
                status_code=response.status_code,
                json_body=self._safe_json(response),
                headers=dict(response.headers),
            )

        raise RetriesExhausted(
            f"Gave up on {method} {path} after {MAX_ATTEMPTS} attempts ({last_detail}).",
            "Jira is rate limiting or unavailable. Wait a few minutes and re-run; any work "
            "already completed is recorded and will not be repeated.",
        )

    def _sleep(self, seconds: float, *, reason: str) -> None:
        logger.info("Backing off %.1fs after %s", seconds, reason)
        time.sleep(seconds)

    def _raise_for_client_error(self, response: httpx.Response, path: str) -> None:
        """Map 4xx to typed errors. 401, 403, and 404 stay distinct, per SC-008."""
        status = response.status_code
        if status < 400:
            return

        if status == 401:
            raise AuthFailure(
                "Jira rejected the credentials (HTTP 401).",
                "Check JIRA_EMAIL and JIRA_API_TOKEN. API tokens can be revoked or expire; "
                "generate a new one at id.atlassian.net under Security.",
            )
        if status == 403:
            raise IssueForbidden(
                f"The account is authenticated but not permitted to access {path} (HTTP 403).",
                "Ask a Jira administrator to grant your account access to this project or issue.",
            )
        if status == 404:
            raise IssueNotFound(
                f"Jira returned HTTP 404 for {path}.",
                "The item does not exist, or your account cannot see it -- Jira does not "
                "distinguish the two. Check the key for typos and confirm you can open it in "
                "a browser while signed in as the same account.",
            )

        detail = self._error_detail(response)

        if status == 400:
            raise JiraBadRequest(
                f"Jira rejected the request to {path} (HTTP 400): {detail}",
                "This is a configuration mismatch, not a transient failure -- retrying "
                "will produce the same result. Check the field(s) named above against "
                "the target project's screen and field configuration.",
                detail=detail,
                field_errors=self._field_errors(response),
            )

        raise JiraTestGenError(
            f"Jira rejected the request with HTTP {status}: {detail}",
            "Check the request details above against your Jira configuration.",
        )

    @staticmethod
    def _field_errors(response: httpx.Response) -> dict[str, str]:
        """Jira's per-field rejection map, which names exactly what to fix."""
        try:
            body = response.json()
        except ValueError:
            return {}
        if isinstance(body, dict) and isinstance(body.get("errors"), dict):
            return {str(k): str(v) for k, v in body["errors"].items()}
        return {}

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """Pull Jira's own error text out, which is far more useful than a bare status."""
        try:
            body = response.json()
        except ValueError:
            return response.text[:300] if response.text else "(no body)"

        if isinstance(body, dict):
            messages = body.get("errorMessages") or []
            field_errors = body.get("errors") or {}
            parts: list[str] = [str(m) for m in messages]
            parts.extend(f"{k}: {v}" for k, v in field_errors.items())
            if parts:
                return "; ".join(parts)
        return str(body)[:300]
