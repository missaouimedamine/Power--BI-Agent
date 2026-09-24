from __future__ import annotations

import pytest
from pydantic import ValidationError

from powerbi_agent.utils.config import AppEnv, Settings, get_settings


def test_defaults_are_safe() -> None:
    s = get_settings()
    assert s.app_env is AppEnv.TESTING
    assert s.allow_publish is False
    assert s.require_confirmation is True
    assert s.powerbi_enabled is False


def test_secrets_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERBI_CLIENT_SECRET", "super-secret-value")
    monkeypatch.setenv("MODEL_API_KEY", "sk-abcdefghijklmnopqrstuv")
    s = Settings()
    assert s.powerbi_client_secret is not None
    assert s.powerbi_client_secret.get_secret_value() == "super-secret-value"
    dumped = str(s.redacted()) + repr(s)
    assert "super-secret-value" not in dumped
    assert "sk-abcdefghijklmnopqrstuv" not in dumped


def test_empty_secret_is_shown_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "")
    assert Settings().redacted()["model_api_key"] == ""


def test_invalid_log_level_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    with pytest.raises(ValidationError):
        Settings()


def test_empty_duckdb_path_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKDB_PATH", "")
    assert Settings().duckdb_path is None


def test_mcp_server_config_holds_no_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERBI_CLIENT_SECRET", "super-secret-value")
    servers = Settings().mcp_servers()
    assert servers[0].name == "powerbi-modeling"
    assert "super-secret-value" not in servers[0].model_dump_json()
