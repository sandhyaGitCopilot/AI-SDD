"""Typed errors, one per exit code in contracts/cli.md.

Every error carries a ``remediation``: SC-008 requires that each failure a user can hit says
what went wrong *and* what to do next. An exception without a remediation is a half-finished
error message, so the base class makes it a required argument rather than an optional nicety.
"""

from __future__ import annotations


class JiraTestGenError(Exception):
    """Base for every error this tool raises deliberately."""

    exit_code: int = 1

    def __init__(self, message: str, remediation: str) -> None:
        super().__init__(message)
        self.message = message
        self.remediation = remediation

    def render(self) -> str:
        """Message and remediation, formatted for stderr."""
        return f"{self.message}\n  -> {self.remediation}"


class InvalidArguments(JiraTestGenError):
    """Malformed issue key or invalid option combination (FR-003)."""

    exit_code = 2


class IssueNotFound(JiraTestGenError):
    """The issue does not exist, or is not visible to this account (FR-003)."""

    exit_code = 3


class IssueForbidden(JiraTestGenError):
    """The account is authenticated but may not read this issue (FR-003)."""

    exit_code = 4


class AuthFailure(JiraTestGenError):
    """Jira rejected the credentials (FR-003)."""

    exit_code = 5


class InsufficientContent(JiraTestGenError):
    """The requirement has no usable content to generate from (FR-004)."""

    exit_code = 6


class MissingProjectPermission(JiraTestGenError):
    """The account lacks CREATE_ISSUES or LINK_ISSUES on the target project (FR-022)."""

    exit_code = 7


class GenerationFailure(JiraTestGenError):
    """The generation service was unreachable, refused, or returned unusable output (FR-031)."""

    exit_code = 8


class NoPendingDraft(JiraTestGenError):
    """No pending draft, or an ambiguous run selection (FR-017a)."""

    exit_code = 9


class DraftValidationFailure(JiraTestGenError):
    """The draft could not be interpreted (FR-016)."""

    exit_code = 10

    def __init__(self, message: str, remediation: str, findings: list[str] | None = None) -> None:
        super().__init__(message, remediation)
        self.findings = findings or []

    def render(self) -> str:
        lines = [self.message]
        lines.extend(f"    {finding}" for finding in self.findings)
        lines.append(f"  -> {self.remediation}")
        return "\n".join(lines)


class PublishFailure(JiraTestGenError):
    """Publishing failed in whole or in part; the run is re-runnable (FR-024)."""

    exit_code = 11


class RetriesExhausted(JiraTestGenError):
    """Retrying against Jira was abandoned (FR-025)."""

    exit_code = 12


class SiteConfigUnresolved(JiraTestGenError):
    """A configured field, issue type, or link type does not exist on this site (research R5)."""

    exit_code = 13


#: Every deliberate error class, for the exit-code contract test (T069).
ERROR_CLASSES: tuple[type[JiraTestGenError], ...] = (
    InvalidArguments,
    IssueNotFound,
    IssueForbidden,
    AuthFailure,
    InsufficientContent,
    MissingProjectPermission,
    GenerationFailure,
    NoPendingDraft,
    DraftValidationFailure,
    PublishFailure,
    RetriesExhausted,
    SiteConfigUnresolved,
)
