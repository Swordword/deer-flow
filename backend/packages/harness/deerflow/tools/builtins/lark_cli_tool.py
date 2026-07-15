"""Controlled lark-cli tools with per-DeerFlow-user auth isolation."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.tools import tool

from deerflow.config import get_app_config
from deerflow.config.lark_cli_config import LarkCliConfig
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime

_SHELL_METACHARS = frozenset(";|&`$<>\n\r")
_SECRET_PATTERNS = (
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(access[_-]?token|accessToken|refresh[_-]?token|refreshToken|app[_-]?secret|appSecret)([\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]+)"),
)
_READONLY_PREFIXES = (
    "agenda",
    "check",
    "describe",
    "download",
    "export",
    "find",
    "get",
    "list",
    "lookup",
    "query",
    "read",
    "retrieve",
    "scopes",
    "search",
    "show",
    "status",
    "whoami",
)


@dataclass(frozen=True)
class _AuthSession:
    user_id: str
    device_code: str
    expires_at: float


_AUTH_SESSIONS: dict[str, _AuthSession] = {}


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _runtime_app_config(runtime: Runtime | None) -> Any:
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        app_config = context.get("app_config")
        if app_config is not None:
            return app_config
    return get_app_config()


def _lark_cli_config(runtime: Runtime | None) -> LarkCliConfig:
    return _runtime_app_config(runtime).lark_cli


def _response(**payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _mask(text: str) -> str:
    masked = text
    for pattern in _SECRET_PATTERNS:
        masked = pattern.sub(lambda m: f"{m.group(1)}{m.group(2) if m.lastindex and m.lastindex >= 2 else ''}***", masked)
    return masked


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return f"{text[:head]}\n...<truncated {len(text) - max_chars} chars>...\n{text[-tail:]}"


def _format_command_result(result: _CommandResult, config: LarkCliConfig) -> dict[str, Any]:
    max_chars = max(config.output_max_chars, 1000)
    stdout_budget = max_chars // 2
    stderr_budget = max_chars - stdout_budget
    return {
        "ok": result.returncode == 0 and not result.timed_out,
        "exit_code": result.returncode,
        "timed_out": result.timed_out,
        "stdout": _truncate(_mask(result.stdout), stdout_budget),
        "stderr": _truncate(_mask(result.stderr), stderr_budget),
    }


def _user_home(runtime: Runtime | None, config: LarkCliConfig) -> Path:
    user_id = resolve_runtime_user_id(runtime)
    root = config.resolved_config_root()
    return root / user_id


def _command_env(user_home: Path) -> dict[str, str]:
    env = os.environ.copy()
    lark_config_dir = user_home / ".lark-cli"
    env.update(
        {
            "HOME": str(user_home),
            "LARKSUITE_CLI_CONFIG_DIR": str(lark_config_dir),
            "XDG_CONFIG_HOME": str(user_home / ".config"),
            "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
            "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
        }
    )
    return env


async def _run_lark_cli(
    config: LarkCliConfig,
    user_home: Path,
    argv: list[str],
    *,
    timeout_seconds: float | None = None,
    stdin_text: str | None = None,
) -> _CommandResult:
    user_home.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        config.executable,
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_command_env(user_home),
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(stdin_text.encode("utf-8") if stdin_text is not None else None),
            timeout=timeout_seconds or config.command_timeout_seconds,
        )
    except TimeoutError:
        proc.kill()
        stdout_bytes, stderr_bytes = await proc.communicate()
        return _CommandResult(
            returncode=proc.returncode if proc.returncode is not None else -9,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            timed_out=True,
        )
    return _CommandResult(
        returncode=proc.returncode if proc.returncode is not None else 0,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
    )


def _config_file(user_home: Path) -> Path:
    return user_home / ".lark-cli" / "config.json"


def _secret_file(user_home: Path) -> Path:
    return user_home / ".lark-cli" / "secrets" / "app_secret"


def _write_secret_file(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value)


def _write_file_backed_config(config: LarkCliConfig, user_home: Path) -> None:
    config_path = _config_file(user_home)
    secret_path = _secret_file(user_home)
    _write_secret_file(secret_path, config.app_secret or "")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "apps": [
            {
                "appId": config.app_id,
                "appSecret": {
                    "source": "file",
                    "id": str(secret_path),
                },
                "brand": config.brand,
                "lang": "zh",
                "users": [],
            }
        ]
    }
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


async def _ensure_configured(config: LarkCliConfig, user_home: Path) -> str | None:
    if _config_file(user_home).is_file():
        return None
    if not config.app_id or not config.app_secret:
        return "lark-cli is not configured for this DeerFlow user, and lark_cli.app_id/app_secret are missing."
    try:
        _write_file_backed_config(config, user_home)
    except OSError as exc:
        return f"Failed to initialize file-backed lark-cli config: {exc}"
    return None


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _validate_argv(argv: list[str], config: LarkCliConfig) -> str | None:
    if not argv:
        return "argv must contain at least one lark-cli subcommand."
    if argv[0] == config.executable or argv[0].endswith("/lark-cli"):
        return "Do not include the lark-cli executable in argv; pass only subcommands and flags."
    for arg in argv:
        if not isinstance(arg, str):
            return "All argv entries must be strings."
        if any(ch in arg for ch in _SHELL_METACHARS):
            return f"Unsafe shell metacharacter found in argument: {arg!r}"
    service = argv[0]
    if service.startswith("-"):
        return "argv must start with an allowed lark-cli service, not a global flag."
    if service not in set(config.allowed_services):
        allowed = ", ".join(sorted(config.allowed_services))
        return f"lark-cli service {service!r} is not allowed. Allowed services: {allowed}."
    if service == "auth" and len(argv) > 1 and argv[1] == "login":
        return "Use lark_cli_auth_start and lark_cli_auth_complete for user login; lark_cli_run does not expose auth login."
    if service == "config":
        return "lark-cli config management is handled by DeerFlow and is not exposed to agents."
    return None


def _is_readonly_argv(argv: list[str]) -> bool:
    service = argv[0]
    if service == "api":
        return len(argv) > 1 and argv[1].upper() == "GET"
    if service == "auth":
        return len(argv) > 1 and argv[1] in {"status", "list", "scopes", "check"}
    action_candidates = [arg for arg in argv[1:] if not arg.startswith("-")]
    if not action_candidates:
        return False
    action = action_candidates[0].lstrip("+").replace("_", "-").lower()
    return action.startswith(_READONLY_PREFIXES)


def _confirmation_required(result: _CommandResult) -> dict[str, Any] | None:
    if result.returncode != 10:
        return None
    payload = _parse_json_object(result.stderr.strip()) or _parse_json_object(result.stdout.strip())
    if not payload:
        return None
    error = payload.get("error")
    if isinstance(error, dict) and error.get("type") == "confirmation_required":
        return error
    return None


def _cleanup_auth_sessions(now: float | None = None) -> None:
    current = now or time.time()
    expired = [session_id for session_id, session in _AUTH_SESSIONS.items() if session.expires_at <= current]
    for session_id in expired:
        _AUTH_SESSIONS.pop(session_id, None)


@tool("lark_cli_auth_start", parse_docstring=True)
async def lark_cli_auth_start_tool(runtime: Runtime) -> str:
    """Start a per-user lark-cli recommended OAuth login.

    Returns a verification URL and auth_session_id. The user must open the URL,
    complete authorization, then ask you to call lark_cli_auth_complete with the
    returned auth_session_id.
    """
    config = _lark_cli_config(runtime)
    if not config.enabled:
        return _response(ok=False, error="lark_cli is disabled.")
    user_home = _user_home(runtime, config)
    config_error = await _ensure_configured(config, user_home)
    if config_error:
        return _response(ok=False, error=config_error)
    result = await _run_lark_cli(
        config,
        user_home,
        ["auth", "login", "--recommend", "--no-wait", "--json"],
        timeout_seconds=config.command_timeout_seconds,
    )
    if result.returncode != 0:
        return _response(**_format_command_result(result, config), error="Failed to start lark-cli auth login.")
    payload = _parse_json_object(result.stdout.strip())
    if not payload or not payload.get("verification_url") or not payload.get("device_code"):
        return _response(ok=False, error="lark-cli auth login returned an unexpected response.", stdout=_mask(result.stdout), stderr=_mask(result.stderr))
    expires_in = int(payload.get("expires_in") or 600)
    session_id = uuid.uuid4().hex
    _cleanup_auth_sessions()
    _AUTH_SESSIONS[session_id] = _AuthSession(
        user_id=resolve_runtime_user_id(runtime),
        device_code=str(payload["device_code"]),
        expires_at=time.time() + max(expires_in, 1),
    )
    return _response(
        ok=True,
        auth_session_id=session_id,
        verification_url=payload["verification_url"],
        expires_in=expires_in,
        message="Open verification_url to authorize, then call lark_cli_auth_complete with auth_session_id.",
    )


@tool("lark_cli_auth_complete", parse_docstring=True)
async def lark_cli_auth_complete_tool(runtime: Runtime, auth_session_id: str) -> str:
    """Complete a per-user lark-cli OAuth login after the user authorizes.

    Args:
        auth_session_id: The short-lived ID returned by lark_cli_auth_start.
    """
    config = _lark_cli_config(runtime)
    if not config.enabled:
        return _response(ok=False, error="lark_cli is disabled.")
    _cleanup_auth_sessions()
    session = _AUTH_SESSIONS.get(auth_session_id)
    if session is None:
        return _response(ok=False, error="Auth session is missing or expired. Start lark-cli auth again.")
    user_id = resolve_runtime_user_id(runtime)
    if session.user_id != user_id:
        return _response(ok=False, error="Auth session belongs to a different DeerFlow user.")
    user_home = _user_home(runtime, config)
    result = await _run_lark_cli(
        config,
        user_home,
        ["auth", "login", "--device-code", session.device_code],
        timeout_seconds=config.auth_timeout_seconds,
    )
    if result.returncode == 0:
        _AUTH_SESSIONS.pop(auth_session_id, None)
    return _response(**_format_command_result(result, config))


@tool("lark_cli_status", parse_docstring=True)
async def lark_cli_status_tool(runtime: Runtime, verify: bool = True) -> str:
    """Check the current DeerFlow user's isolated lark-cli login status.

    Args:
        verify: Whether to verify the token with the Feishu/Lark server.
    """
    config = _lark_cli_config(runtime)
    if not config.enabled:
        return _response(ok=False, error="lark_cli is disabled.")
    user_home = _user_home(runtime, config)
    if not _config_file(user_home).is_file():
        return _response(ok=False, authenticated=False, error="lark-cli is not initialized for this DeerFlow user. Call lark_cli_auth_start first.")
    argv = ["auth", "status"]
    if verify:
        argv.append("--verify")
    result = await _run_lark_cli(config, user_home, argv, timeout_seconds=config.command_timeout_seconds)
    return _response(**_format_command_result(result, config), authenticated=result.returncode == 0)


@tool("lark_cli_run", parse_docstring=True)
async def lark_cli_run_tool(
    runtime: Runtime,
    argv: list[str],
    confirmed: bool = False,
    timeout_seconds: float | None = None,
) -> str:
    """Run an allowed lark-cli command for the current DeerFlow user.

    Args:
        argv: lark-cli arguments excluding the executable, for example ["contact", "+search-user", "--query", "Alice"].
        confirmed: Set true only after the user explicitly confirms a high-risk write.
        timeout_seconds: Optional command timeout override.
    """
    config = _lark_cli_config(runtime)
    if not config.enabled:
        return _response(ok=False, error="lark_cli is disabled.")
    validation_error = _validate_argv(argv, config)
    if validation_error:
        return _response(ok=False, error=validation_error)
    if not config.allow_writes and not _is_readonly_argv(argv):
        return _response(ok=False, error="lark-cli write or ambiguous command rejected because lark_cli.allow_writes is false.")
    if timeout_seconds is not None and timeout_seconds <= 0:
        return _response(ok=False, error="timeout_seconds must be positive.")
    user_home = _user_home(runtime, config)
    if not _config_file(user_home).is_file():
        return _response(ok=False, error="lark-cli is not initialized for this DeerFlow user. Call lark_cli_auth_start first.")
    effective_argv = list(argv)
    if confirmed and "--yes" not in effective_argv:
        effective_argv.append("--yes")
    result = await _run_lark_cli(config, user_home, effective_argv, timeout_seconds=timeout_seconds)
    confirmation = _confirmation_required(result)
    if confirmation is not None and not confirmed:
        return _response(
            ok=False,
            confirmation_required=True,
            risk=confirmation.get("risk"),
            message=confirmation.get("message") or "lark-cli command requires confirmation.",
        )
    return _response(**_format_command_result(result, config))
