"""Pre-write permission gate (FR-022, research R2).

Split out of ``writer.py`` so the check runs during ``generate`` without pulling in the
publishing code -- and so User Story 1 and User Story 3 never edit the same file.

Two details that are easy to get wrong:

* The ``permissions`` query parameter is **mandatory**. A bare call to ``mypermissions`` is
  rejected, so "just ask for everything" does not work.
* ``LINK_ISSUES`` is checked alongside ``CREATE_ISSUES``, and a missing link permission is a
  hard stop. An account that can create but not link would produce orphan test cases that
  satisfy nothing -- FR-020 requires the link.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from jira_testgen.errors import AuthFailure, MissingProjectPermission
from jira_testgen.jira.client import JiraClient

logger = logging.getLogger("jira_testgen.jira.permissions")

CREATE_ISSUES = "CREATE_ISSUES"
LINK_ISSUES = "LINK_ISSUES"
REQUIRED_PERMISSIONS = (CREATE_ISSUES, LINK_ISSUES)


@dataclass(frozen=True)
class PermissionReport:
    project_key: str
    granted: dict[str, bool]

    @property
    def missing(self) -> list[str]:
        return [name for name, ok in self.granted.items() if not ok]

    @property
    def ok(self) -> bool:
        return not self.missing


def check_permissions(client: JiraClient, project_key: str) -> PermissionReport:
    """Ask Jira which of the required permissions this account holds on the project."""
    response = client.get(
        "/mypermissions",
        params={
            "projectKey": project_key,
            "permissions": ",".join(REQUIRED_PERMISSIONS),
        },
    )
    body = response.json_body or {}
    permissions = body.get("permissions") or {}

    granted = {
        name: bool((permissions.get(name) or {}).get("havePermission", False))
        for name in REQUIRED_PERMISSIONS
    }
    logger.info("Permissions on %s: %s", project_key, granted)
    return PermissionReport(project_key=project_key, granted=granted)


def require_publish_permissions(client: JiraClient, project_key: str) -> PermissionReport:
    """Stop before anything is created when the account cannot finish the job (FR-022)."""
    report = check_permissions(client, project_key)
    if report.ok:
        return report

    missing = ", ".join(report.missing)
    explanations = {
        CREATE_ISSUES: "create test case issues",
        LINK_ISSUES: "link each test case back to the requirement",
    }
    needs = "; ".join(explanations[name] for name in report.missing)

    raise MissingProjectPermission(
        f"Your account lacks {missing} on project {project_key}.",
        f"Ask a Jira administrator to grant {missing} on {project_key} so the tool can {needs}. "
        "Nothing has been created.",
    )


def verify_authentication(client: JiraClient) -> str:
    """Confirm the credentials actually authenticate, before anything else runs.

    This exists because of a real Atlassian Cloud behaviour, found by running against a
    live site (T073): most read endpoints do **not** return 401 for a bad token. They
    serve the request anonymously and return ``200`` with an empty result.

    The practical consequence was a badly misleading error. Site configuration is resolved
    first (contracts/cli.md step 2), so the tool's first call was ``GET /issueLinkType``.
    With an invalid token that returned ``200`` and zero link types, and the user was told:

        This Jira site has no issue link type named 'Relates'.
        -> Available link types: (none returned).

    -- exit 13, sending them to configure link types when their token was simply wrong.
    contracts/cli.md is explicit that exit 5 must be distinguishable from a configuration
    problem, and SC-008 requires the message to name what actually went wrong.

    ``/myself`` is one of the endpoints that *does* honour authentication, so one cheap
    read against it makes exit 5 reliable. It runs before site resolution, costs a single
    request, and returns the account id for the log.
    """
    response = client.get("/myself")
    body = response.json_body or {}
    account_id = str(body.get("accountId") or "")

    # A 200 with no identity is the anonymous case: Jira served the request without
    # accepting the credentials. Treated as an auth failure, because that is what it is.
    if not account_id:
        raise AuthFailure(
            "Jira accepted the request but did not recognise the account.",
            "Check JIRA_EMAIL and JIRA_API_TOKEN. Jira answers some endpoints anonymously "
            "when a token is wrong, so a bad token can look like a configuration problem "
            "rather than a login one.",
        )

    logger.info("Authenticated as account %s", account_id)
    return account_id
