"""T076, T077: the `.env` parser and the precedence rule (FR-026a, FR-026c).

Two properties carry the weight here.

**Precedence.** An exported variable must beat the file. A user who exports a fresh token
to work around a stale `.env` would otherwise be silently overridden by the file they were
working around, and the symptom -- an auth failure they cannot explain -- points nowhere
near the cause.

**The error must not quote the value.** A malformed `.env` line is reported by line number
only. The thing on that line is a credential; printing it to explain that it is malformed
would leak it to the terminal, the scrollback, and whatever CI captured the run, which is
exactly what FR-027 forbids.

Every test passes an explicit path. None of them depends on the process's working
directory, and none can pick up a real `.env` from the repository root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jira_testgen.config import (
    DOTENV_FILENAME,
    load_dotenv_file,
    load_settings,
)
from jira_testgen.errors import InvalidArguments

pytestmark = pytest.mark.unit

TOKEN = "fake-jira-token-value-aaaa"


def write_env(tmp_path: Path, body: str) -> Path:
    path = tmp_path / DOTENV_FILENAME
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestParsing:
    def test_a_simple_assignment(self, tmp_path: Path) -> None:
        path = write_env(tmp_path, "JIRA_EMAIL=you@example.com\n")
        assert load_dotenv_file(path) == {"JIRA_EMAIL": "you@example.com"}

    def test_several_assignments_keep_their_values(self, tmp_path: Path) -> None:
        path = write_env(
            tmp_path,
            "JIRA_BASE_URL=https://acme.atlassian.net\nJIRA_EMAIL=you@example.com\n",
        )
        assert load_dotenv_file(path) == {
            "JIRA_BASE_URL": "https://acme.atlassian.net",
            "JIRA_EMAIL": "you@example.com",
        }

    @pytest.mark.parametrize(
        "line",
        [
            "JIRA_EMAIL=you@example.com",
            "  JIRA_EMAIL=you@example.com",
            "JIRA_EMAIL = you@example.com",
            "JIRA_EMAIL\t=\tyou@example.com",
            "JIRA_EMAIL=you@example.com   ",
        ],
    )
    def test_whitespace_is_tolerated(self, tmp_path: Path, line: str) -> None:
        assert load_dotenv_file(write_env(tmp_path, line + "\n")) == {
            "JIRA_EMAIL": "you@example.com"
        }

    @pytest.mark.parametrize(
        "line",
        ['JIRA_EMAIL="you@example.com"', "JIRA_EMAIL='you@example.com'"],
    )
    def test_surrounding_quotes_are_stripped(self, tmp_path: Path, line: str) -> None:
        assert load_dotenv_file(write_env(tmp_path, line + "\n")) == {
            "JIRA_EMAIL": "you@example.com"
        }

    def test_an_unmatched_quote_is_kept_verbatim(self, tmp_path: Path) -> None:
        """Only a matched pair is stripped. A lone quote is more likely part of a token
        than a syntax error, and guessing would corrupt the credential silently."""
        path = write_env(tmp_path, 'JIRA_API_TOKEN="abc\n')
        assert load_dotenv_file(path) == {"JIRA_API_TOKEN": '"abc'}

    def test_an_export_prefix_is_tolerated(self, tmp_path: Path) -> None:
        """README and quickstart both document `export NAME=value`. Users paste those
        lines straight into `.env`, so rejecting the prefix would fail the single most
        likely way this file gets written."""
        path = write_env(tmp_path, 'export JIRA_API_TOKEN="abc123"\n')
        assert load_dotenv_file(path) == {"JIRA_API_TOKEN": "abc123"}

    def test_a_value_may_contain_equals_signs(self, tmp_path: Path) -> None:
        """Split on the first `=` only -- base64 and query strings contain more."""
        path = write_env(tmp_path, "ANTHROPIC_API_KEY=sk-ant-a=b=c\n")
        assert load_dotenv_file(path) == {"ANTHROPIC_API_KEY": "sk-ant-a=b=c"}

    def test_an_inline_hash_is_part_of_the_value(self, tmp_path: Path) -> None:
        """Not treated as a comment. A `#` is a legal character in a token, and stripping
        from it would silently truncate the credential -- a failure that looks like a bad
        token rather than a parsing bug."""
        path = write_env(tmp_path, "JIRA_API_TOKEN=abc#123\n")
        assert load_dotenv_file(path) == {"JIRA_API_TOKEN": "abc#123"}

    def test_an_empty_value_is_allowed(self, tmp_path: Path) -> None:
        path = write_env(tmp_path, "JIRA_TARGET_PROJECT=\n")
        assert load_dotenv_file(path) == {"JIRA_TARGET_PROJECT": ""}

    def test_crlf_line_endings_are_handled(self, tmp_path: Path) -> None:
        """A `.env` written by a Windows editor. Leaving the `\\r` on would append it to
        the token and produce an auth failure with no visible cause."""
        path = tmp_path / DOTENV_FILENAME
        path.write_bytes(b"JIRA_EMAIL=you@example.com\r\nJIRA_API_TOKEN=abc\r\n")
        assert load_dotenv_file(path) == {
            "JIRA_EMAIL": "you@example.com",
            "JIRA_API_TOKEN": "abc",
        }

    def test_a_utf8_bom_is_stripped(self, tmp_path: Path) -> None:
        path = tmp_path / DOTENV_FILENAME
        path.write_bytes(b"\xef\xbb\xbfJIRA_EMAIL=you@example.com\n")
        assert load_dotenv_file(path) == {"JIRA_EMAIL": "you@example.com"}


class TestSkippedLines:
    def test_blank_lines_are_skipped(self, tmp_path: Path) -> None:
        path = write_env(tmp_path, "\n\nJIRA_EMAIL=you@example.com\n\n")
        assert load_dotenv_file(path) == {"JIRA_EMAIL": "you@example.com"}

    def test_comments_are_skipped(self, tmp_path: Path) -> None:
        path = write_env(tmp_path, "# Jira credentials\nJIRA_EMAIL=you@example.com\n  # indented\n")
        assert load_dotenv_file(path) == {"JIRA_EMAIL": "you@example.com"}

    def test_a_file_of_only_comments_yields_nothing(self, tmp_path: Path) -> None:
        assert load_dotenv_file(write_env(tmp_path, "# nothing here\n\n")) == {}


class TestMissingFile:
    def test_a_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        """FR-026a. The file is optional; exporting variables must keep working."""
        assert load_dotenv_file(tmp_path / DOTENV_FILENAME) == {}

    def test_a_none_path_is_not_an_error(self, tmp_path: Path) -> None:
        assert load_dotenv_file(None) == {}

    def test_a_directory_in_place_of_the_file_is_not_an_error(self, tmp_path: Path) -> None:
        (tmp_path / DOTENV_FILENAME).mkdir()
        assert load_dotenv_file(tmp_path / DOTENV_FILENAME) == {}


# ---------------------------------------------------------------------------
# Malformed content (FR-026c)
# ---------------------------------------------------------------------------


class TestMalformed:
    def test_a_line_without_an_equals_sign_is_fatal(self, tmp_path: Path) -> None:
        path = write_env(tmp_path, "JIRA_EMAIL=you@example.com\nthis is not an assignment\n")
        with pytest.raises(InvalidArguments) as exc:
            load_dotenv_file(path)
        assert exc.value.exit_code == 2

    def test_it_names_the_file_and_the_line_number(self, tmp_path: Path) -> None:
        """1-based, counting every physical line, so it matches what the editor shows."""
        path = write_env(
            tmp_path,
            "# a comment\nJIRA_EMAIL=you@example.com\n\nbroken line here\n",
        )
        with pytest.raises(InvalidArguments) as exc:
            load_dotenv_file(path)
        rendered = exc.value.render()
        assert DOTENV_FILENAME in rendered
        assert "4" in rendered

    def test_it_does_not_quote_the_offending_value(self, tmp_path: Path) -> None:
        """FR-026c. The thing on that line is a secret."""
        path = write_env(tmp_path, f"JIRA_API_TOKEN {TOKEN}\n")
        with pytest.raises(InvalidArguments) as exc:
            load_dotenv_file(path)
        rendered = exc.value.render()
        assert TOKEN not in rendered
        assert "JIRA_API_TOKEN" not in rendered

    def test_an_empty_name_is_fatal(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidArguments):
            load_dotenv_file(write_env(tmp_path, "=orphaned-value\n"))

    def test_the_remediation_says_what_to_do(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidArguments) as exc:
            load_dotenv_file(write_env(tmp_path, "nonsense\n"))
        assert exc.value.remediation.strip()

    def test_an_unreadable_file_is_fatal_not_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proceeding as though it were absent produces "variable not set" while the user
        is looking at the variable, which is the most confusing failure available."""
        path = write_env(tmp_path, "JIRA_EMAIL=you@example.com\n")

        def boom(*args: object, **kwargs: object) -> bytes:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "read_bytes", boom)
        with pytest.raises(InvalidArguments) as exc:
            load_dotenv_file(path)
        assert "read" in exc.value.message.lower() or "read" in exc.value.remediation.lower()


# ---------------------------------------------------------------------------
# T077 -- precedence
# ---------------------------------------------------------------------------


def complete_env(tmp_path: Path, **overrides: str) -> Path:
    values = {
        "JIRA_BASE_URL": "https://from-file.atlassian.net",
        "JIRA_EMAIL": "file@example.com",
        "JIRA_API_TOKEN": "file-token",
        "ANTHROPIC_API_KEY": "sk-ant-file-key",
    }
    values.update(overrides)
    return write_env(tmp_path, "".join(f"{k}={v}\n" for k, v in values.items()))


class TestPrecedence:
    def test_the_file_supplies_names_absent_from_the_environment(self, tmp_path: Path) -> None:
        settings = load_settings(dotenv_path=complete_env(tmp_path))
        assert settings.jira.base_url == "https://from-file.atlassian.net"
        assert settings.jira.email == "file@example.com"
        assert settings.jira.api_token.reveal() == "file-token"

    def test_the_environment_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR-026a. A stale file must never override an export the user just made."""
        monkeypatch.setenv("JIRA_API_TOKEN", "exported-token")
        settings = load_settings(dotenv_path=complete_env(tmp_path))
        assert settings.jira.api_token.reveal() == "exported-token"

    def test_the_environment_wins_per_name_not_wholesale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One exported variable must not suppress the rest of the file."""
        monkeypatch.setenv("JIRA_EMAIL", "exported@example.com")
        settings = load_settings(dotenv_path=complete_env(tmp_path))
        assert settings.jira.email == "exported@example.com"
        assert settings.jira.api_token.reveal() == "file-token"

    def test_an_empty_exported_value_does_not_shadow_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty export is indistinguishable from unset elsewhere in this module --
        `_require` already strips and rejects it -- so the file should fill it."""
        monkeypatch.setenv("JIRA_API_TOKEN", "   ")
        settings = load_settings(dotenv_path=complete_env(tmp_path))
        assert settings.jira.api_token.reveal() == "file-token"

    def test_a_name_in_neither_still_raises_the_usual_error(self, tmp_path: Path) -> None:
        partial = write_env(tmp_path, "JIRA_BASE_URL=https://acme.atlassian.net\n")
        with pytest.raises(InvalidArguments) as exc:
            load_settings(dotenv_path=partial)
        assert "JIRA_EMAIL" in exc.value.message

    def test_non_secret_settings_load_from_the_file_too(self, tmp_path: Path) -> None:
        path = complete_env(tmp_path, JIRA_TARGET_PROJECT="QA", JIRA_ISSUE_TYPE="Test")
        settings = load_settings(dotenv_path=path)
        assert settings.site.target_project == "QA"
        assert settings.site.issue_type_name == "Test"

    def test_cli_options_still_beat_the_file(self, tmp_path: Path) -> None:
        """The established precedence chain gains a bottom rung, it does not change:
        CLI option > environment > .env > default."""
        path = complete_env(tmp_path, JIRA_TARGET_PROJECT="QA")
        settings = load_settings(dotenv_path=path, target_project="OVERRIDE")
        assert settings.site.target_project == "OVERRIDE"

    def test_no_dotenv_still_works_from_the_environment_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name, value in {
            "JIRA_BASE_URL": "https://acme.atlassian.net",
            "JIRA_EMAIL": "you@example.com",
            "JIRA_API_TOKEN": "tok",
            "ANTHROPIC_API_KEY": "sk-ant-x",
        }.items():
            monkeypatch.setenv(name, value)
        settings = load_settings(dotenv_path=None)
        assert settings.jira.email == "you@example.com"


class TestTheFileIsNotLeakedIntoTheProcess:
    def test_loading_does_not_mutate_os_environ(self, tmp_path: Path) -> None:
        """Values are resolved, not exported. Mutating the environment would leak
        credentials into every subprocess the tool later spawns -- an editor opened for
        review, for instance (see review.open_in_editor)."""
        import os

        before = dict(os.environ)
        load_settings(dotenv_path=complete_env(tmp_path))
        assert dict(os.environ) == before
