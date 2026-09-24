"""Application configuration.

All settings come from environment variables (optionally a ``.env`` file). Secrets are
typed as :class:`pydantic.SecretStr` so they are never printed by ``repr`` or logged.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class McpServerConfig(BaseModel):
    """Transport-agnostic description of an MCP server the agent may use.

    Only *names* of environment variables are stored here, never their values, so the
    object can be serialised into generated config files without leaking secrets.
    """

    name: str
    command: list[str]
    enabled: bool = True
    env_vars: list[str] = Field(default_factory=list)
    description: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnv = AppEnv.DEVELOPMENT
    log_level: str = "INFO"
    log_format: str = Field(default="json", pattern="^(json|console)$")

    # LLM provider (used by OpenCode / future in-process agents).
    model_provider: str = ""
    model_name: str = ""
    model_api_key: SecretStr | None = None

    # Power BI / Fabric.
    powerbi_enabled: bool = False
    powerbi_tenant_id: str = ""
    powerbi_client_id: str = ""
    powerbi_client_secret: SecretStr | None = None
    powerbi_workspace_id: str = ""
    powerbi_mcp_command: str = "npx -y @microsoft/powerbi-modeling-mcp@latest --start"

    # Local storage.
    workspaces_dir: Path = Path("workspaces")
    duckdb_path: Path | None = None

    # Safety switches. Publishing is hard-disabled until Phase 8 is implemented.
    allow_publish: bool = False
    require_confirmation: bool = True

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"invalid log level: {value}")
        return value

    @field_validator("duckdb_path", mode="before")
    @classmethod
    def _empty_path_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION

    def mcp_servers(self) -> list[McpServerConfig]:
        """MCP servers known to the platform. Credentials are passed via env var names."""
        return [
            McpServerConfig(
                name="powerbi-modeling",
                command=self.powerbi_mcp_command.split(),
                enabled=self.powerbi_enabled,
                env_vars=[],
                description=(
                    "Microsoft Power BI Modeling MCP server (semantic model authoring via "
                    "Power BI Desktop, Fabric workspace, or PBIP folder). Authenticates "
                    "interactively; no secrets are passed through this config."
                ),
            ),
        ]

    def redacted(self) -> dict[str, Any]:
        """Settings as a dict with every secret masked; safe to print or log."""
        data = self.model_dump(mode="json")
        for key, value in self.__dict__.items():
            if isinstance(value, SecretStr):
                # Show whether a secret is set without revealing it.
                data[key] = "**********" if value.get_secret_value() else ""
        return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
