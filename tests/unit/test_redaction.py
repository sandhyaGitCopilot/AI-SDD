"""T019: credentials must never reach output, in any form (FR-027).

The realistic leak is not a deliberate print. It is an httpx debug log, an exception repr
carrying request headers, or a traceback. These tests exercise those paths specifically.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest
import respx

from jira_testgen.config import (
    REDACTED,
    JiraSettings,
    RedactionFilter,
    Secret,
    configure_run_logging,
    install_redaction,
    redact,
)
from jira_testgen.errors import AuthFailure
from jira_testgen.jira.client import JiraClient

pytestmark = pytest.mark.unit

TOKEN = "ATATT3xFfGF0-super-secret-token-value"
API_KEY = "sk-ant-api03-another-secret-value"


class TestSecretType:
    def test_repr_and_str_are_redacted(self) -> None:
        secret = Secret(TOKEN)
        assert repr(secret) == REDACTED
        assert str(secret) == REDACTED
        assert f"token={secret}" == f"token={REDACTED}"

    def test_value_is_still_reachable_deliberately(self) -> None:
        assert Secret(TOKEN).reveal() == TOKEN

    def test_fstring_cannot_leak(self) -> None:
        """The whole point: an accidental interpolation prints nothing useful."""
        message = f"Authenticating with {Secret(TOKEN)}"
        assert TOKEN not in message

    def test_dataclass_repr_does_not_leak(self) -> None:
        settings = JiraSettings(
            base_url="https://x.atlassian.net", email="a@b.c", api_token=Secret(TOKEN)
        )
        assert TOKEN not in repr(settings)

    def test_equality_and_hash(self) -> None:
        assert Secret(TOKEN) == Secret(TOKEN)
        assert Secret(TOKEN) != Secret("other")
        assert len({Secret(TOKEN), Secret(TOKEN)}) == 1
        assert not Secret("")


class TestRedactionFilter:
    def _capture(self, caplog: pytest.LogCaptureFixture, logger_name: str = "t") -> logging.Logger:
        logger = logging.getLogger(logger_name)
        logger.addFilter(RedactionFilter([TOKEN, API_KEY]))
        return logger

    def test_scrubs_formatted_message(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = self._capture(caplog, "t.message")
        with caplog.at_level(logging.DEBUG):
            logger.warning("Calling Jira with token %s", TOKEN)
        assert TOKEN not in caplog.text
        assert REDACTED in caplog.text

    def test_scrubs_lazy_args(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = self._capture(caplog, "t.args")
        with caplog.at_level(logging.DEBUG):
            logger.info("headers=%s", {"Authorization": f"Basic {TOKEN}"})
        assert TOKEN not in caplog.text

    def test_scrubs_dict_args(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = self._capture(caplog, "t.dict")
        with caplog.at_level(logging.DEBUG):
            logger.info("%(k)s", {"k": TOKEN})
        assert TOKEN not in caplog.text

    def test_scrubs_exception_text(self, caplog: pytest.LogCaptureFixture) -> None:
        """A traceback carrying the credential is the realistic leak."""
        logger = self._capture(caplog, "t.exc")
        with caplog.at_level(logging.DEBUG):
            try:
                raise RuntimeError(f"auth failed for Basic {TOKEN}")
            except RuntimeError:
                logger.exception("request failed")
        assert TOKEN not in caplog.text

    def test_handles_several_secrets(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = self._capture(caplog, "t.multi")
        with caplog.at_level(logging.DEBUG):
            logger.info("jira=%s anthropic=%s", TOKEN, API_KEY)
        assert TOKEN not in caplog.text
        assert API_KEY not in caplog.text

    def test_empty_secret_list_is_a_no_op(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("t.empty")
        logger.addFilter(RedactionFilter([]))
        with caplog.at_level(logging.DEBUG):
            logger.info("nothing secret here")
        assert "nothing secret here" in caplog.text

    def test_overlapping_secrets_fully_scrubbed(self, caplog: pytest.LogCaptureFixture) -> None:
        """Longest-first replacement, so a secret containing another is replaced whole."""
        short, long = "abc123", "abc123-extended-secret"
        logger = logging.getLogger("t.overlap")
        logger.addFilter(RedactionFilter([short, long]))
        with caplog.at_level(logging.DEBUG):
            logger.info("value=%s", long)
        assert long not in caplog.text


class TestRedactHelper:
    def test_scrubs_arbitrary_text(self) -> None:
        text = f"POST failed: Basic {TOKEN}"
        assert TOKEN not in redact(text, [Secret(TOKEN)])

    def test_accepts_raw_strings_and_secrets(self) -> None:
        out = redact(f"{TOKEN} and {API_KEY}", [Secret(TOKEN), API_KEY])
        assert TOKEN not in out
        assert API_KEY not in out

    def test_ignores_empty_secrets(self) -> None:
        assert redact("unchanged", [Secret(""), ""]) == "unchanged"


class TestEndToEndLeakPaths:
    @respx.mock
    def test_auth_failure_message_has_no_token(self) -> None:
        respx.get("https://example.atlassian.net/rest/api/3/field").mock(
            return_value=httpx.Response(401)
        )
        settings = JiraSettings(
            base_url="https://example.atlassian.net", email="a@b.c", api_token=Secret(TOKEN)
        )
        with JiraClient(settings) as client, pytest.raises(AuthFailure) as exc:
            client.get("/field")

        assert TOKEN not in exc.value.render()
        assert TOKEN not in str(exc.value)

    @respx.mock
    def test_httpx_debug_logging_is_scrubbed(self, tmp_path: Path) -> None:
        """httpx logs through its own logger -- filtering ours alone would not catch it."""
        log_path = configure_run_logging(tmp_path, [Secret(TOKEN)], verbose=True)
        install_redaction([Secret(TOKEN)], logging.getLogger("httpx"))
        httpx_logger = logging.getLogger("httpx")
        httpx_logger.setLevel(logging.DEBUG)
        for handler in logging.getLogger("jira_testgen").handlers:
            httpx_logger.addHandler(handler)

        httpx_logger.debug("send_request_headers.started request=<Authorization: Basic %s>", TOKEN)

        content = log_path.read_text(encoding="utf-8")
        assert TOKEN not in content
        assert REDACTED in content

    def test_run_log_file_never_contains_secrets(self, tmp_path: Path) -> None:
        log_path = configure_run_logging(tmp_path, [Secret(TOKEN), Secret(API_KEY)])
        logger = logging.getLogger("jira_testgen.test")
        logger.info("starting with %s", TOKEN)
        logger.error("generation key %s rejected", API_KEY)

        content = log_path.read_text(encoding="utf-8")
        assert TOKEN not in content
        assert API_KEY not in content

    @pytest.fixture(autouse=True)
    def _reset_logging(self) -> None:
        """Keep handlers from leaking between tests in this class."""
        yield
        logger = logging.getLogger("jira_testgen")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        logger.filters.clear()


class TestDotenvSourcedSecretsAreRedactedIdentically:
    """T078: a credential from `.env` gets the same treatment as an exported one (FR-026b).

    This is the assertion that keeps `.env` support from quietly becoming a hole in FR-027.
    The redaction filter keys on the live secret *values*, so it cannot care where a value
    came from -- but "cannot" is a claim about the current implementation, and the point of
    a test is that it stays true of the next one.
    """

    DOTENV_TOKEN = "dotenv-sourced-jira-token-zzz"
    DOTENV_KEY = "sk-ant-dotenv-sourced-key-zzz"

    def _write_dotenv(self, tmp_path: Path) -> Path:
        from jira_testgen.config import DOTENV_FILENAME

        path = tmp_path / DOTENV_FILENAME
        path.write_text(
            "JIRA_BASE_URL=https://acme.atlassian.net\n"
            "JIRA_EMAIL=you@example.com\n"
            f"JIRA_API_TOKEN={self.DOTENV_TOKEN}\n"
            f"ANTHROPIC_API_KEY={self.DOTENV_KEY}\n",
            encoding="utf-8",
        )
        return path

    def test_the_secret_type_still_refuses_to_print_itself(self, tmp_path: Path) -> None:
        from jira_testgen.config import load_settings

        settings = load_settings(dotenv_path=self._write_dotenv(tmp_path))
        assert str(settings.jira.api_token) == REDACTED
        assert repr(settings.jira.api_token) == REDACTED
        assert self.DOTENV_TOKEN not in f"{settings.jira.api_token}"

    def test_it_is_scrubbed_from_the_run_log(self, tmp_path: Path) -> None:
        from jira_testgen.config import load_settings

        settings = load_settings(dotenv_path=self._write_dotenv(tmp_path))
        log_path = configure_run_logging(tmp_path / "run", list(settings.secrets()))

        logger = logging.getLogger("jira_testgen.test")
        logger.info("authenticating with %s", self.DOTENV_TOKEN)
        logger.error("generation key %s rejected", self.DOTENV_KEY)

        content = log_path.read_text(encoding="utf-8")
        assert self.DOTENV_TOKEN not in content
        assert self.DOTENV_KEY not in content
        assert REDACTED in content

    def test_it_is_scrubbed_from_a_traceback(self, tmp_path: Path) -> None:
        """The realistic leak path: an exception carrying an auth header."""
        from jira_testgen.config import load_settings

        settings = load_settings(dotenv_path=self._write_dotenv(tmp_path))
        log_path = configure_run_logging(tmp_path / "run", list(settings.secrets()))
        logger = logging.getLogger("jira_testgen.test")

        try:
            raise RuntimeError(f"request failed: Authorization: Basic {self.DOTENV_TOKEN}")
        except RuntimeError:
            logger.exception("the request blew up")

        content = log_path.read_text(encoding="utf-8")
        assert self.DOTENV_TOKEN not in content

    def test_redact_scrubs_a_dotenv_value(self, tmp_path: Path) -> None:
        from jira_testgen.config import load_settings, redact

        settings = load_settings(dotenv_path=self._write_dotenv(tmp_path))
        text = f"POST failed with Authorization: Basic {self.DOTENV_TOKEN}"
        assert self.DOTENV_TOKEN not in redact(text, list(settings.secrets()))

    def test_the_dotenv_path_is_never_logged_with_its_contents(self, tmp_path: Path) -> None:
        """FR-026b forbids recording the file's values. Logging that a file was read is
        fine and useful; logging what was in it is not."""
        from jira_testgen.config import load_settings

        path = self._write_dotenv(tmp_path)
        log_path = configure_run_logging(tmp_path / "run", [])
        settings = load_settings(dotenv_path=path)
        configure_run_logging(tmp_path / "run", list(settings.secrets()))

        content = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        assert self.DOTENV_TOKEN not in content
        assert self.DOTENV_KEY not in content

    @pytest.fixture(autouse=True)
    def _reset_logging(self) -> None:
        yield
        logger = logging.getLogger("jira_testgen")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        logger.filters.clear()
