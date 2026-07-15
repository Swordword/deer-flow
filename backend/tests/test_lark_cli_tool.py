from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.config.lark_cli_config import LarkCliConfig
from deerflow.tools.builtins import lark_cli_tool


def _runtime(user_id: str, config: LarkCliConfig) -> SimpleNamespace:
    return SimpleNamespace(context={"user_id": user_id, "app_config": SimpleNamespace(lark_cli=config)})


def _enabled_config(tmp_path: Path, **overrides) -> LarkCliConfig:
    return LarkCliConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret-token",
        config_root=str(tmp_path),
        **overrides,
    )


def _decode(payload: str) -> dict:
    return json.loads(payload)


def test_lark_cli_uses_distinct_per_user_homes(tmp_path: Path) -> None:
    config = _enabled_config(tmp_path)

    user_a_home = lark_cli_tool._user_home(_runtime("user-a", config), config)
    user_b_home = lark_cli_tool._user_home(_runtime("user-b", config), config)

    assert user_a_home == tmp_path / "user-a"
    assert user_b_home == tmp_path / "user-b"
    assert user_a_home != user_b_home


@pytest.mark.asyncio
async def test_ensure_configured_writes_file_backed_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _enabled_config(tmp_path)
    user_home = tmp_path / "user-a"

    async def fail_run(*_args, **_kwargs):
        raise AssertionError("config init should not be called")

    monkeypatch.setattr(lark_cli_tool, "_run_lark_cli", fail_run)

    error = await lark_cli_tool._ensure_configured(config, user_home)

    assert error is None
    config_data = json.loads((user_home / ".lark-cli" / "config.json").read_text(encoding="utf-8"))
    app = config_data["apps"][0]
    assert app["appId"] == "cli_test"
    assert app["appSecret"]["source"] == "file"
    secret_path = Path(app["appSecret"]["id"])
    assert secret_path == user_home / ".lark-cli" / "secrets" / "app_secret"
    assert secret_path.read_text(encoding="utf-8") == "secret-token"


@pytest.mark.asyncio
async def test_auth_start_returns_session_without_device_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _enabled_config(tmp_path)
    runtime = _runtime("user-a", config)

    async def fake_ensure_configured(_config, _user_home):
        return None

    async def fake_run(_config, _user_home, argv, **_kwargs):
        assert argv == ["auth", "login", "--recommend", "--no-wait", "--json"]
        return lark_cli_tool._CommandResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "device_code": "device-secret",
                    "verification_url": "https://accounts.feishu.cn/oauth/v1/device/verify?x=1",
                    "expires_in": 600,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(lark_cli_tool, "_ensure_configured", fake_ensure_configured)
    monkeypatch.setattr(lark_cli_tool, "_run_lark_cli", fake_run)
    lark_cli_tool._AUTH_SESSIONS.clear()

    response = _decode(await lark_cli_tool.lark_cli_auth_start_tool.coroutine(runtime))

    assert response["ok"] is True
    assert response["verification_url"] == "https://accounts.feishu.cn/oauth/v1/device/verify?x=1"
    assert "auth_session_id" in response
    assert "device_code" not in response
    assert lark_cli_tool._AUTH_SESSIONS[response["auth_session_id"]].device_code == "device-secret"


@pytest.mark.asyncio
async def test_auth_complete_uses_stored_device_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _enabled_config(tmp_path)
    runtime = _runtime("user-a", config)
    session_id = "session-1"
    lark_cli_tool._AUTH_SESSIONS.clear()
    lark_cli_tool._AUTH_SESSIONS[session_id] = lark_cli_tool._AuthSession(
        user_id="user-a",
        device_code="device-secret",
        expires_at=9999999999,
    )
    calls: list[list[str]] = []

    async def fake_run(_config, _user_home, argv, **_kwargs):
        calls.append(argv)
        return lark_cli_tool._CommandResult(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr(lark_cli_tool, "_run_lark_cli", fake_run)

    response = _decode(await lark_cli_tool.lark_cli_auth_complete_tool.coroutine(runtime, session_id))

    assert response["ok"] is True
    assert calls == [["auth", "login", "--device-code", "device-secret"]]
    assert session_id not in lark_cli_tool._AUTH_SESSIONS


@pytest.mark.asyncio
async def test_run_lark_cli_uses_create_subprocess_exec_without_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _enabled_config(tmp_path)
    user_home = tmp_path / "user-a"
    captured: dict = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, input=None):
            captured["input"] = input
            return b"{}", b""

        def kill(self):
            captured["killed"] = True

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await lark_cli_tool._run_lark_cli(config, user_home, ["auth", "list"])

    assert result.returncode == 0
    assert captured["args"] == ("lark-cli", "auth", "list")
    assert "shell" not in captured["kwargs"]
    assert captured["kwargs"]["env"]["HOME"] == str(user_home)
    assert captured["kwargs"]["env"]["LARKSUITE_CLI_CONFIG_DIR"] == str(user_home / ".lark-cli")


@pytest.mark.asyncio
async def test_run_requires_user_login_before_command(tmp_path: Path) -> None:
    config = _enabled_config(tmp_path)
    runtime = _runtime("user-a", config)

    response = _decode(await lark_cli_tool.lark_cli_run_tool.coroutine(runtime, ["contact", "+search-user", "--query", "Alice"]))

    assert response["ok"] is False
    assert "not initialized" in response["error"]


@pytest.mark.asyncio
async def test_run_rejects_write_when_writes_disabled(tmp_path: Path) -> None:
    config = _enabled_config(tmp_path)
    runtime = _runtime("user-a", config)
    (tmp_path / "user-a" / ".lark-cli").mkdir(parents=True)
    (tmp_path / "user-a" / ".lark-cli" / "config.json").write_text("{}", encoding="utf-8")

    response = _decode(await lark_cli_tool.lark_cli_run_tool.coroutine(runtime, ["drive", "+delete", "--file-token", "tok"]))

    assert response["ok"] is False
    assert "allow_writes is false" in response["error"]


@pytest.mark.asyncio
async def test_confirmation_required_is_not_auto_confirmed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _enabled_config(tmp_path, allow_writes=True)
    runtime = _runtime("user-a", config)
    (tmp_path / "user-a" / ".lark-cli").mkdir(parents=True)
    (tmp_path / "user-a" / ".lark-cli" / "config.json").write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []

    async def fake_run(_config, _user_home, argv, **_kwargs):
        calls.append(argv)
        return lark_cli_tool._CommandResult(
            returncode=10,
            stdout="",
            stderr=json.dumps(
                {
                    "ok": False,
                    "error": {
                        "type": "confirmation_required",
                        "message": "drive +delete requires confirmation",
                        "risk": {"level": "high-risk-write", "action": "drive +delete"},
                    },
                }
            ),
        )

    monkeypatch.setattr(lark_cli_tool, "_run_lark_cli", fake_run)

    response = _decode(await lark_cli_tool.lark_cli_run_tool.coroutine(runtime, ["drive", "+delete", "--file-token", "tok"]))

    assert response["ok"] is False
    assert response["confirmation_required"] is True
    assert calls == [["drive", "+delete", "--file-token", "tok"]]
    assert "--yes" not in calls[0]


def test_mask_redacts_sensitive_values() -> None:
    text = 'Authorization: Bearer abc123\n{"access_token":"secret","app_secret":"top","appSecret":"camel"}'

    masked = lark_cli_tool._mask(text)

    assert "abc123" not in masked
    assert "top" not in masked
    assert "camel" not in masked
    assert "Bearer ***" in masked
    assert '"access_token":"***"' in masked
    assert '"app_secret":"***"' in masked
    assert '"appSecret":"***"' in masked
