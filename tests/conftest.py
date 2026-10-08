"""Shared test fixtures.

The ``isolate_environment`` fixture is autouse and deliberately aggressive: it strips every
credential from the environment for every test. A test that needs credentials must set them
itself. This is what makes the guarantee in quickstart.md real -- the suite passes with no
credentials configured, and anything that tries to reach a real service fails loudly instead
of silently talking to someone's Jira site.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_MANAGED_VARS = (
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_API_TOKEN",
    "JIRA_TARGET_PROJECT",
    "JIRA_AC_FIELD",
    "JIRA_ISSUE_TYPE",
    "JIRA_LINK_TYPE",
    "ANTHROPIC_API_KEY",
)


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every credential and site setting from the environment, and ignore any `.env`.

    The second half matters as much as the first and is easier to forget. Once
    ``load_settings`` reads a ``.env`` file from the working directory (FR-026a), a
    developer who keeps real credentials in one at the repository root would have them
    injected into every test in this suite -- which would both falsify the guarantee that
    the suite passes with no credentials configured and let a test reach somebody's real
    Jira site while appearing to use a mock.

    So credential discovery is pointed at nothing at all. A test that wants `.env`
    behaviour passes an explicit path (see ``tests/unit/test_dotenv.py``), which is also
    the only way those tests stay independent of where pytest was invoked from.
    """
    for var in _MANAGED_VARS:
        monkeypatch.delenv(var, raising=False)

    from jira_testgen import config

    monkeypatch.setattr(config, "default_dotenv_path", lambda: None)


@pytest.fixture
def jira_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Fake but well-formed Jira credentials for tests that need a configured client."""
    values = {
        "JIRA_BASE_URL": "https://example.atlassian.net",
        "JIRA_EMAIL": "tester@example.com",
        "JIRA_API_TOKEN": "fake-jira-token-value",
        "ANTHROPIC_API_KEY": "sk-ant-fake-key-value",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """An isolated run workspace."""
    root = tmp_path / ".jira-testgen"
    root.mkdir()
    return root


@pytest.fixture
def fixtures_dir() -> Path:
    """Directory holding checked-in sample files (including spreadsheet-saved CSVs)."""
    return Path(__file__).parent / "fixtures"
