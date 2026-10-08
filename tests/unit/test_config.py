"""T079: the tool never writes to `.env` (FR-026b).

A credential file the tool only ever reads is a much easier thing to reason about than one
it might rewrite. If `jira-testgen` could touch `.env`, every question about that file --
is my comment still there, did it reformat my token, did it reorder anything -- would need
an answer. Asserting the bytes are untouched means the answer is always no.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jira_testgen.config import DOTENV_FILENAME, default_dotenv_path, load_settings

pytestmark = pytest.mark.unit

BODY = """\
# Jira credentials -- never commit this file
JIRA_BASE_URL=https://acme.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=real-looking-token-value

# Generation
ANTHROPIC_API_KEY=sk-ant-real-looking-key
JIRA_TARGET_PROJECT=QA
"""


@pytest.fixture
def dotenv(tmp_path: Path) -> Path:
    path = tmp_path / DOTENV_FILENAME
    path.write_text(BODY, encoding="utf-8")
    return path


class TestTheToolNeverWritesToDotenv:
    def test_the_bytes_are_unchanged_after_loading(self, dotenv: Path) -> None:
        before = dotenv.read_bytes()
        load_settings(dotenv_path=dotenv)
        assert dotenv.read_bytes() == before

    def test_comments_and_blank_lines_survive(self, dotenv: Path) -> None:
        load_settings(dotenv_path=dotenv)
        assert dotenv.read_text(encoding="utf-8") == BODY

    def test_the_modification_time_is_unchanged(self, dotenv: Path) -> None:
        """Belt and braces: an open in write mode that wrote nothing would still touch
        mtime, and would still be a write this requirement forbids."""
        before = dotenv.stat().st_mtime_ns
        load_settings(dotenv_path=dotenv)
        assert dotenv.stat().st_mtime_ns == before

    def test_loading_twice_changes_nothing(self, dotenv: Path) -> None:
        before = dotenv.read_bytes()
        load_settings(dotenv_path=dotenv)
        load_settings(dotenv_path=dotenv)
        assert dotenv.read_bytes() == before

    def test_no_dotenv_is_created_when_none_exists(self, tmp_path: Path) -> None:
        """A missing file stays missing. Helpfully scaffolding one would put an empty
        credential file into a directory the user did not ask to have one in."""
        from jira_testgen.errors import InvalidArguments

        target = tmp_path / DOTENV_FILENAME
        with pytest.raises(InvalidArguments):
            load_settings(dotenv_path=target)  # no credentials anywhere
        assert not target.exists()

    def test_no_sibling_or_backup_file_is_left_behind(self, dotenv: Path) -> None:
        load_settings(dotenv_path=dotenv)
        assert sorted(p.name for p in dotenv.parent.iterdir()) == [DOTENV_FILENAME]


class TestDefaultDiscovery:
    def test_it_looks_for_dotenv_in_the_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Resolved at call time against the cwd, so the answer follows the user's shell
        rather than wherever the package happens to be installed."""
        monkeypatch.chdir(tmp_path)
        assert default_dotenv_path() == tmp_path / DOTENV_FILENAME

    def test_the_autouse_fixture_has_disabled_discovery_for_this_suite(self) -> None:
        """Guards the guard (T075). If this ever returns a real path, a developer's own
        `.env` could feed credentials into every test in the suite.

        Note this asserts on the patched module attribute, which is the thing
        ``load_settings`` actually calls.
        """
        from jira_testgen import config

        assert config.default_dotenv_path() is None
