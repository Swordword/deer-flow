from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from deerflow.config.runtime_paths import runtime_home


class RepositoryConfig(BaseModel):
    """Administrator-approved repository exposed to Agent snapshot tools."""

    url: str = Field(description="Credential-free HTTPS clone URL.")
    default_ref: str = Field(default="main", description="Branch, tag, or commit used when the tool omits ref.")
    workspace_name: str | None = Field(default=None, description="Optional directory name under workspace/repos.")

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("repository URL must use HTTPS")
        if parsed.username or parsed.password:
            raise ValueError("repository URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("repository URL must not contain a query or fragment")
        return value

    @field_validator("default_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        value = value.strip()
        if not value or value.startswith("-") or any(ch in value for ch in "\x00\n\r"):
            raise ValueError("repository default_ref is invalid")
        return value

    @field_validator("workspace_name")
    @classmethod
    def _validate_workspace_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or not value.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise ValueError("repository workspace_name must contain only letters, digits, '.', '-' or '_'")
        return value


class RepoSnapshotConfig(BaseModel):
    """Controlled, persistent local repository snapshots for Agent workflows."""

    enabled: bool = Field(default=False, description="Expose repository snapshot tools to configured agents.")
    git_executable: str = Field(default="git", description="Git executable name or absolute path.")
    cache_root: str = Field(
        default="git-cache",
        description="Shared bare-mirror cache. Relative paths resolve from DEER_FLOW_HOME.",
    )
    repositories: dict[str, RepositoryConfig] = Field(
        default_factory=dict,
        description="Administrator-approved repository aliases. Agents cannot supply arbitrary clone URLs.",
    )
    username: str = Field(default="oauth2", description="HTTPS Git username supplied through GIT_ASKPASS.")
    token: str | None = Field(default=None, description="HTTPS Git token. Prefer an environment-variable reference.")
    command_timeout_seconds: float = Field(default=300.0, gt=0, description="Timeout for each Git command.")
    output_max_chars: int = Field(default=4000, ge=1000, description="Maximum Git error output returned to the model.")

    @field_validator("git_executable", "username")
    @classmethod
    def _validate_nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value

    @field_validator("repositories")
    @classmethod
    def _validate_aliases(cls, value: dict[str, RepositoryConfig]) -> dict[str, RepositoryConfig]:
        for alias in value:
            if not alias or not alias.replace("-", "").replace("_", "").replace(".", "").isalnum():
                raise ValueError("repository aliases must contain only letters, digits, '.', '-' or '_'")
        return value

    def resolved_cache_root(self) -> Path:
        path = Path(self.cache_root)
        if not path.is_absolute():
            path = runtime_home() / path
        return path.resolve()
