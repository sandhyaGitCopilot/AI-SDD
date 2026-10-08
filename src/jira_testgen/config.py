"""Settings, secrets, and credential redaction (FR-026a-c, FR-027, research R8).

Credentials resolve in one order, highest first: **CLI option > environment variable >
`.env` file > default**. The `.env` rung was added after research R8 originally rejected
any file-based source; R8 now carries the amendment and the reasoning. The file is read
and never written, and its values are never exported into ``os.environ`` -- doing so would
hand them to every subprocess the tool later spawns.

Two mechanisms protect credentials, because one is not enough:

1. ``Secret`` wraps every credential so a stray f-string or a traceback repr prints ``***``.
2. ``RedactionFilter`` scrubs the *values* out of every log record, which catches the paths
   a wrapper cannot reach -- httpx debug logs, third-party library logging, exception text
   built by code that never saw the ``Secret`` object.

The filter keys on live secret values rather than on header names. A rule like "never log
Authorization" only catches the leak its author remembered; keying on the value catches the
ones they did not.
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from jira_testgen.errors import InvalidArguments

REDACTED = "***"

#: Effort levels the Claude API accepts for output_config.
Effort = Literal["low", "medium", "high", "xhigh", "max"]

DEFAULT_WORKSPACE = Path(".jira-testgen")
DEFAULT_ISSUE_TYPE = "Task"
DEFAULT_LINK_TYPE = "Relates"
DEFAULT_MAX_CASES = 25
HARD_MAX_CASES = 25


class Secret:
    """A credential that refuses to print itself.

    ``repr`` and ``str`` both return ``***``, so interpolating a Secret into a message or
    letting it surface in a traceback cannot leak the value. Call ``reveal()`` at the exact
    point the value is needed and nowhere else.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return REDACTED

    def __str__(self) -> str:  # pragma: no cover - trivial
        return REDACTED

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Secret):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)


class RedactionFilter(logging.Filter):
    """Scrub known secret values out of every log record.

    Rewrites the formatted message and stringifies args, because a secret can arrive either
    already-formatted or as a lazy ``%s`` argument. Exception text is scrubbed too -- that is
    the realistic leak, not ordinary logging.
    """

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        # Longest first, so a secret containing another as a substring is replaced whole.
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text

    def _scrub_arg(self, value: object) -> object:
        """Scrub a log argument, preserving its type unless it actually holds a secret."""
        if isinstance(value, str):
            return self._scrub(value)
        if isinstance(value, (int, float, bool)) or value is None:
            return value  # cannot contain a secret, and %d needs the original type
        rendered = str(value)
        scrubbed = self._scrub(rendered)
        return scrubbed if scrubbed != rendered else value

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True

        if isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)

        if record.args:
            # Only replace an argument when it actually carries a secret. Blanket-stringifying
            # every arg would break numeric format specifiers like %d, which is a bug the
            # redaction would then hide behind a logging error.
            if isinstance(record.args, dict):
                record.args = {k: self._scrub_arg(v) for k, v in record.args.items()}
            else:
                record.args = tuple(self._scrub_arg(a) for a in record.args)

        # A traceback is the realistic leak path, and it needs care: at filter time
        # `exc_text` is still None, because logging only populates it when a formatter runs.
        # So format the exception here and cache the scrubbed result -- Formatter reuses a
        # non-empty `exc_text` rather than re-deriving it from `exc_info`.
        if record.exc_info and not record.exc_text:
            record.exc_text = "".join(traceback.format_exception(*record.exc_info))
        if record.exc_text:
            record.exc_text = self._scrub(record.exc_text)

        if record.stack_info:
            record.stack_info = self._scrub(record.stack_info)

        return True


def install_redaction(
    secrets: Sequence[Secret | str], logger: logging.Logger | None = None
) -> RedactionFilter:
    """Attach a redaction filter to a logger and all of its existing handlers.

    Handlers are filtered as well as the logger: a record emitted by a *child* logger reaches
    a handler without passing the parent logger's filters, so filtering only the logger would
    leave httpx's own log records unscrubbed.
    """
    values = [s.reveal() if isinstance(s, Secret) else s for s in secrets]
    redaction = RedactionFilter(values)
    target = logger if logger is not None else logging.getLogger()
    target.addFilter(redaction)
    for handler in target.handlers:
        handler.addFilter(redaction)
    return redaction


@dataclass(frozen=True)
class JiraSettings:
    base_url: str
    email: str
    api_token: Secret

    @property
    def api_root(self) -> str:
        return f"{self.base_url.rstrip('/')}/rest/api/3"

    def browse_url(self, issue_key: str) -> str:
        return f"{self.base_url.rstrip('/')}/browse/{issue_key}"


@dataclass(frozen=True)
class GenerationSettings:
    api_key: Secret
    model: str = "claude-opus-5-5"
    effort: Effort = "high"
    max_cases: int = DEFAULT_MAX_CASES

    @property
    def service_label(self) -> str:
        """Recorded in the draft so a reviewer can see what produced the cases (FR-030)."""
        return f"anthropic/{self.model}"


@dataclass(frozen=True)
class SiteSettings:
    """Per-site names that vary between Jira instances (research R5)."""

    target_project: str | None = None
    ac_field_name: str | None = None
    issue_type_name: str = DEFAULT_ISSUE_TYPE
    link_type_name: str = DEFAULT_LINK_TYPE


@dataclass(frozen=True)
class Settings:
    jira: JiraSettings
    generation: GenerationSettings
    site: SiteSettings
    workspace: Path = field(default=DEFAULT_WORKSPACE)

    def secrets(self) -> list[Secret]:
        return [self.jira.api_token, self.generation.api_key]


DOTENV_FILENAME = ".env"

#: Lines the parser skips without comment (FR-026c).
_DOTENV_COMMENT = "#"


def default_dotenv_path() -> Path | None:
    """Where to look for a `.env` file: the current working directory.

    Resolved at call time rather than at import, so the answer follows the user's shell
    instead of wherever the package happens to be installed. Patched out wholesale by the
    test suite -- see ``tests/conftest.py`` -- because a developer's real `.env` must not
    be able to feed credentials into a test run.
    """
    return Path.cwd() / DOTENV_FILENAME


def load_dotenv_file(path: Path | None) -> dict[str, str]:
    """Parse a `.env` file into a plain dict (FR-026a, FR-026c).

    Read-only by construction: this function opens the file for reading and nothing in
    this module ever writes to it (FR-026b). The parse is deliberately small -- no
    third-party dependency for `NAME=value`, which would enlarge the dependency surface
    for a dozen lines of string handling.

    Tolerant where tolerance is safe:

    * an optional ``export `` prefix, because the README and quickstart both document
      shell ``export NAME=value`` lines and users paste them straight in
    * whitespace around the name and the ``=``
    * one matched pair of surrounding quotes
    * CRLF line endings and a UTF-8 BOM, both of which a Windows editor produces

    Strict where tolerance would corrupt a credential:

    * a value is split on the **first** ``=`` only, so base64 padding survives
    * ``#`` inside a value is part of the value, not a comment -- stripping from it would
      silently truncate a token, and the resulting auth failure would look like a bad
      credential rather than a parsing bug
    * only a *matched* pair of quotes is stripped; a lone quote is kept
    * anything else non-blank raises, rather than being skipped (FR-026c). Skipping would
      produce "variable not set" while the user is looking straight at the variable

    A missing file, or ``None``, yields an empty dict: the file is optional.
    """
    if path is None:
        return {}

    try:
        if not path.is_file():
            return {}
        raw = path.read_bytes()
    except OSError as exc:
        raise InvalidArguments(
            f"The credentials file {path} exists but could not be read: {exc.strerror}.",
            "Check the file's permissions, or remove it and export the variables "
            "instead. See README.md for the list.",
        ) from exc

    text = raw.decode("utf-8-sig", errors="replace")
    values: dict[str, str] = {}

    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(_DOTENV_COMMENT):
            continue

        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()

        name, separator, value = stripped.partition("=")
        name = name.strip()

        # The message names the line number and nothing else. Whatever is on that line is
        # a credential, so quoting it to explain the problem would leak it to the
        # terminal, the scrollback, and any CI log (FR-026c, FR-027).
        if not separator or not name:
            raise InvalidArguments(
                f"{path} line {number} is not a NAME=value assignment.",
                f"Each line must read NAME=value, or start with {_DOTENV_COMMENT} to be a "
                f"comment. Fix line {number} and re-run. The line's contents are not shown "
                "here because this file holds credentials.",
            )

        values[name] = _unquote(value.strip())

    return values


def _unquote(value: str) -> str:
    """Strip one matched pair of surrounding quotes, and only a matched pair."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _resolve(name: str, overrides: dict[str, str]) -> str:
    """Look a name up: the real environment first, then the `.env` values (FR-026a).

    The environment wins per name, not wholesale. A user who exports one fresh credential
    to work around a stale file must not have that export silently overridden -- and must
    not lose the rest of the file either. An exported-but-blank value counts as unset,
    matching what ``_require`` already does with one.
    """
    live = os.environ.get(name, "").strip()
    if live:
        return live
    return overrides.get(name, "").strip()


def _require(name: str, purpose: str, overrides: dict[str, str]) -> str:
    value = _resolve(name, overrides)
    if not value:
        raise InvalidArguments(
            f"Required environment variable {name} is not set.",
            f"Export {name} with {purpose}, or put it in a {DOTENV_FILENAME} file in this "
            f"directory (copy {DOTENV_FILENAME}.example to start). See README.md for the "
            "full list.",
        )
    return value


def load_settings(
    *,
    target_project: str | None = None,
    ac_field: str | None = None,
    issue_type: str | None = None,
    link_type: str | None = None,
    max_cases: int = DEFAULT_MAX_CASES,
    workspace: Path | None = None,
    require_generation: bool = True,
    dotenv_path: Path | None = None,
) -> Settings:
    """Resolve settings, with CLI options taking precedence.

    Precedence, highest first: **CLI option > environment variable > `.env` file >
    default** (FR-026a). The `.env` rung is the newest and the lowest for a reason -- a
    stale file must never override a credential the user has just exported, because the
    symptom (an auth failure they cannot explain) points nowhere near the cause.

    The `.env` file is read, never written (FR-026b), and its values are *not* exported
    into ``os.environ``: doing so would leak credentials into every subprocess the tool
    later spawns, the editor opened during review among them (see ``review.open_in_editor``).

    ``dotenv_path`` is injectable so tests need not depend on the process's working
    directory. Left as ``None`` it falls back to ``default_dotenv_path()``, which the test
    suite patches to return ``None`` so that no test can read a real `.env`
    (see ``tests/conftest.py``).
    """
    if max_cases < 1 or max_cases > HARD_MAX_CASES:
        raise InvalidArguments(
            f"--max-cases must be between 1 and {HARD_MAX_CASES}; got {max_cases}.",
            f"The cap of {HARD_MAX_CASES} keeps a draft reviewable. Re-run with a value in range, "
            "or split the requirement into smaller issues.",
        )

    from_file = load_dotenv_file(dotenv_path or default_dotenv_path())

    jira = JiraSettings(
        base_url=_require(
            "JIRA_BASE_URL", "your Jira site URL, e.g. https://acme.atlassian.net", from_file
        ),
        email=_require("JIRA_EMAIL", "the account email for your Jira API token", from_file),
        api_token=Secret(
            _require("JIRA_API_TOKEN", "a Jira API token from id.atlassian.net", from_file)
        ),
    )

    generation_key = _resolve("ANTHROPIC_API_KEY", from_file)
    if require_generation and not generation_key:
        raise InvalidArguments(
            "Required environment variable ANTHROPIC_API_KEY is not set.",
            f"Export ANTHROPIC_API_KEY with an Anthropic API key, or put it in a "
            f"{DOTENV_FILENAME} file in this directory. Note that this tool sends "
            "requirement text to an external AI service.",
        )

    generation = GenerationSettings(api_key=Secret(generation_key), max_cases=max_cases)

    site = SiteSettings(
        target_project=target_project or _resolve("JIRA_TARGET_PROJECT", from_file) or None,
        ac_field_name=ac_field or _resolve("JIRA_AC_FIELD", from_file) or None,
        issue_type_name=issue_type or _resolve("JIRA_ISSUE_TYPE", from_file) or DEFAULT_ISSUE_TYPE,
        link_type_name=link_type or _resolve("JIRA_LINK_TYPE", from_file) or DEFAULT_LINK_TYPE,
    )

    settings = Settings(
        jira=jira,
        generation=generation,
        site=site,
        workspace=workspace or DEFAULT_WORKSPACE,
    )
    install_redaction(list(settings.secrets()))
    return settings


RUN_LOG_FILENAME = "run.log"


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per line.

    Structured rather than free text because the run log exists to diagnose a *failed*
    run after the fact: the useful questions are "which case failed", "what did Jira
    return", "was a generation request made at all", and answering those by eye against
    wrapped prose is slower than it needs to be. One object per line keeps it greppable
    and `jq`-able without giving up tailing it.

    Redaction happens in the filter, before this runs, so the values interpolated here
    are already scrubbed.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info or record.exc_text:
            # The filter has already formatted and scrubbed this; never re-derive it
            # from exc_info here, which would reintroduce the unredacted traceback.
            payload["exception"] = record.exc_text or ""
        return json.dumps(payload, default=str)


def configure_run_logging(
    run_dir: Path, secrets: Sequence[Secret | str], verbose: bool = False
) -> Path:
    """Attach a structured, redacted log file to this run's directory (T071).

    Idempotent per directory. ``generate`` configures logging and then hands off to the
    publish sequence, which configures it again for the same run; without this guard
    every record after the handoff would be written twice.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / RUN_LOG_FILENAME

    root = logging.getLogger("jira_testgen")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    for existing in root.handlers:
        if getattr(existing, "_jira_testgen_run_log", None) == str(log_path):
            return log_path

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(JsonLinesFormatter())
    handler.addFilter(
        RedactionFilter([s.reveal() if isinstance(s, Secret) else s for s in secrets])
    )
    handler._jira_testgen_run_log = str(log_path)  # type: ignore[attr-defined]

    root.addHandler(handler)
    install_redaction(secrets, root)
    return log_path


def redact(text: str, secrets: Sequence[Secret | str]) -> str:
    """Scrub secret values out of arbitrary text before it is stored or displayed."""
    values = sorted(
        {(s.reveal() if isinstance(s, Secret) else s) for s in secrets if s},
        key=len,
        reverse=True,
    )
    for value in values:
        text = text.replace(value, REDACTED)
    return text


def describe_settings(settings: Settings) -> dict[str, Any]:
    """Settings as a plain dict for logging -- contains no secret values by construction."""
    return {
        "jira_base_url": settings.jira.base_url,
        "jira_email": settings.jira.email,
        "target_project": settings.site.target_project,
        "ac_field_name": settings.site.ac_field_name,
        "issue_type_name": settings.site.issue_type_name,
        "link_type_name": settings.site.link_type_name,
        "model": settings.generation.model,
        "max_cases": settings.generation.max_cases,
    }
