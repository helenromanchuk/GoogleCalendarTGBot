import runpy

import pytest

from app import config
from app.config import settings


def test_testing_true_when_env_var_set(monkeypatch) -> None:
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("TESTING", "true")
    assert settings.testing is True


def test_testing_false_when_env_var_unset(monkeypatch) -> None:
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("TESTING", "false")
    assert settings.testing is False


@pytest.mark.parametrize("database_url", ["sqlite:///:memory:", ""])
def test_cloud_run_rejects_missing_or_sqlite_database(monkeypatch, database_url):
    monkeypatch.setenv("K_SERVICE", "calendarbot-agent")
    monkeypatch.setenv("DATABASE_URL", database_url)
    with pytest.raises(ValueError, match="PostgreSQL DATABASE_URL"):
        runpy.run_path(config.__file__)


def test_cloud_run_accepts_postgresql_database(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "calendarbot-agent")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/calendarbot")
    loaded = runpy.run_path(config.__file__)
    assert loaded["settings"].database_url == "postgresql://user:pass@host/calendarbot"


def test_testing_forced_false_on_cloud_run_even_if_testing_true(monkeypatch) -> None:
    """K_SERVICE (set by Cloud Run) must always win, so the mock-user shortcut
    can never be active in a real deployment even if TESTING=true leaks into
    the environment (e.g. via a copy-pasted .env)."""
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.setenv("K_SERVICE", "calendarbot-agent")
    assert settings.testing is False
