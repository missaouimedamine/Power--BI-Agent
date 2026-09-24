from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from powerbi_agent.utils.config import Settings, get_settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Run every test with a clean environment and no .env file from the repo."""
    for var in [
        "APP_ENV",
        "LOG_LEVEL",
        "MODEL_API_KEY",
        "POWERBI_CLIENT_SECRET",
        "POWERBI_ENABLED",
        "ALLOW_PUBLISH",
        "WORKSPACES_DIR",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "workspaces"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def sales_csv() -> Path:
    return FIXTURES / "sales_small.csv"
