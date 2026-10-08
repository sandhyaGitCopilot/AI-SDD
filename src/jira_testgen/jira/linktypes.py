"""Runtime discovery of site-specific configuration (research R5).

Custom field ids, link type names, and issue type names differ per Jira site. Hardcoding any
of them breaks on every site but the author's, so all three are resolved and verified here.

This runs *before* generation, not before publishing. Discovering a bad link type after a
model call has already been paid for is a worse experience than failing in two seconds, and
the information needed to fail early is available either way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from jira_testgen.errors import SiteConfigUnresolved
from jira_testgen.jira.client import JiraClient

logger = logging.getLogger("jira_testgen.jira.linktypes")


@dataclass(frozen=True)
class ResolvedLinkType:
    name: str
    inward: str
    outward: str


@dataclass(frozen=True)
class ResolvedIssueType:
    id: str
    name: str


@dataclass(frozen=True)
class SiteConfig:
    link_type: ResolvedLinkType
    issue_type: ResolvedIssueType
    ac_field_id: str | None
    ac_field_name: str | None


def resolve_link_type(client: JiraClient, wanted: str) -> ResolvedLinkType:
    """Match the configured link type by name, inward, or outward label."""
    response = client.get("/issueLinkType")
    body = response.json_body or {}
    types = body.get("issueLinkTypes") or []

    target = wanted.strip().casefold()
    for entry in types:
        candidates = {
            str(entry.get("name", "")).casefold(),
            str(entry.get("inward", "")).casefold(),
            str(entry.get("outward", "")).casefold(),
        }
        if target in candidates:
            return ResolvedLinkType(
                name=str(entry.get("name", wanted)),
                inward=str(entry.get("inward", "")),
                outward=str(entry.get("outward", "")),
            )

    available = ", ".join(sorted(str(t.get("name", "?")) for t in types)) or "(none returned)"
    raise SiteConfigUnresolved(
        f"This Jira site has no issue link type named {wanted!r}.",
        f"Available link types: {available}. Re-run with --link-type set to one of them, "
        "or set JIRA_LINK_TYPE.",
    )


def resolve_issue_type(client: JiraClient, project_key: str, wanted: str) -> ResolvedIssueType:
    """Confirm the test case issue type exists in the target project.

    Uses create metadata rather than the global issue type list, because an issue type can
    exist on the site but not be available in this project -- which fails at create time
    with a message that does not explain itself.
    """
    response = client.get(
        "/issue/createmeta",
        params={"projectKeys": project_key, "expand": "projects.issuetypes.fields"},
    )
    body = response.json_body or {}
    projects = body.get("projects") or []

    if not projects:
        raise SiteConfigUnresolved(
            f"Jira returned no create metadata for project {project_key!r}.",
            "Check the project key, and confirm your account can create issues in it. "
            "`jira-testgen` needs create access to the target project.",
        )

    issue_types = projects[0].get("issuetypes") or []
    target = wanted.strip().casefold()
    for entry in issue_types:
        if str(entry.get("name", "")).casefold() == target:
            return ResolvedIssueType(id=str(entry.get("id")), name=str(entry.get("name")))

    available = ", ".join(sorted(str(t.get("name", "?")) for t in issue_types)) or "(none)"
    raise SiteConfigUnresolved(
        f"Project {project_key} has no issue type named {wanted!r}.",
        f"Available issue types in {project_key}: {available}. Re-run with --issue-type set "
        "to one of them, or set JIRA_ISSUE_TYPE.",
    )


def resolve_ac_field(client: JiraClient, wanted: str | None) -> tuple[str | None, str | None]:
    """Find the acceptance criteria field by name.

    Returns ``(field_id, field_name)``. With no name configured, returns ``(None, None)`` and
    the caller falls back to the description. A name that was configured but does not exist
    is an error, not a silent fallback: quietly ignoring it would make the field look empty
    rather than misconfigured, and the user would never learn why their criteria vanished.
    """
    if not wanted or not wanted.strip():
        return None, None

    response = client.get("/field")
    fields = response.json_body or []
    target = wanted.strip().casefold()

    for field in fields:
        if str(field.get("name", "")).casefold() == target:
            return str(field.get("id")), str(field.get("name"))

    custom = sorted(str(f.get("name", "?")) for f in fields if f.get("custom") and f.get("name"))
    preview = ", ".join(custom[:25]) or "(none)"
    suffix = f" (showing 25 of {len(custom)})" if len(custom) > 25 else ""
    raise SiteConfigUnresolved(
        f"This Jira site has no field named {wanted!r}.",
        f"Custom fields available{suffix}: {preview}. Re-run with --ac-field set to the exact "
        "field name, or omit it to read criteria from the description.",
    )


def resolve_site_config(
    client: JiraClient,
    *,
    project_key: str,
    link_type_name: str,
    issue_type_name: str,
    ac_field_name: str | None,
) -> SiteConfig:
    """Resolve everything site-specific in one pass, before any expensive work."""
    ac_field_id, resolved_ac_name = resolve_ac_field(client, ac_field_name)
    link_type = resolve_link_type(client, link_type_name)
    issue_type = resolve_issue_type(client, project_key, issue_type_name)

    logger.info(
        "Resolved site config: link=%s issue_type=%s(%s) ac_field=%s",
        link_type.name,
        issue_type.name,
        issue_type.id,
        resolved_ac_name or "(description)",
    )
    return SiteConfig(
        link_type=link_type,
        issue_type=issue_type,
        ac_field_id=ac_field_id,
        ac_field_name=resolved_ac_name,
    )
