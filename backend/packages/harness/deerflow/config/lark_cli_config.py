from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from deerflow.config.runtime_paths import resolve_path

DEFAULT_LARK_CLI_ALLOWED_SERVICES = [
    "auth",
    "contact",
    "docs",
    "drive",
    "wiki",
    "sheets",
    "calendar",
    "task",
    "minutes",
    "vc",
]


class LarkCliConfig(BaseModel):
    """Configuration for the controlled lark-cli Agent tools."""

    enabled: bool = Field(default=False, description="Expose controlled lark-cli tools to configured agents.")
    executable: str = Field(default="lark-cli", description="lark-cli executable name or absolute path.")
    config_root: str = Field(
        default=".deer-flow/lark-cli-users",
        description="Per-DeerFlow-user lark-cli state root. Relative paths resolve from the project root.",
    )
    brand: Literal["feishu", "lark"] = Field(default="feishu", description="Brand passed to lark-cli config init.")
    app_id: str | None = Field(default=None, description="Feishu/Lark app ID. Prefer an env-var reference in config.yaml.")
    app_secret: str | None = Field(default=None, description="Feishu/Lark app secret. Prefer an env-var reference in config.yaml.")
    allow_writes: bool = Field(default=False, description="Allow lark-cli write operations. Read-only by default.")
    allowed_services: list[str] = Field(
        default_factory=lambda: list(DEFAULT_LARK_CLI_ALLOWED_SERVICES),
        description="Top-level lark-cli services agents may call.",
    )
    command_timeout_seconds: float = Field(default=60.0, gt=0, description="Default lark-cli command timeout.")
    auth_timeout_seconds: float = Field(default=300.0, gt=0, description="Timeout for auth completion polling.")
    output_max_chars: int = Field(default=12000, ge=1000, description="Maximum combined command output returned to the model.")

    @field_validator("executable")
    @classmethod
    def _validate_executable(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("lark_cli.executable must not be empty")
        return value

    @field_validator("allowed_services")
    @classmethod
    def _validate_allowed_services(cls, value: list[str]) -> list[str]:
        services = []
        for item in value:
            service = item.strip()
            if not service:
                continue
            services.append(service)
        return services

    def resolved_config_root(self) -> Path:
        return resolve_path(self.config_root)
