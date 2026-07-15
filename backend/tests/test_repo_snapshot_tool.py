from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.config.paths import Paths
from deerflow.config.repo_snapshot_config import RepoSnapshotConfig
from deerflow.tools.builtins import repo_snapshot_tool


def _config(tmp_path: Path, **overrides: object) -> RepoSnapshotConfig:
    values: dict[str, object] = {
        "enabled": True,
        "cache_root": str(tmp_path / "cache"),
        "repositories": {
            "frontend": {
                "url": "https://gitlab.example.com/group/frontend.git",
                "default_ref": "main",
            }
        },
    }
    values.update(overrides)
    return RepoSnapshotConfig(**values)


def _runtime(config: RepoSnapshotConfig, *, user_id: str = "user-a", thread_id: str = "thread-a") -> SimpleNamespace:
    return SimpleNamespace(
        context={"user_id": user_id, "thread_id": thread_id, "app_config": SimpleNamespace(repo_snapshot=config)},
        config={"configurable": {"thread_id": thread_id}},
    )


def test_repository_config_rejects_credentials_and_non_https_urls() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        RepoSnapshotConfig(repositories={"bad": {"url": "ssh://git@gitlab.example.com/group/repo.git"}})

    with pytest.raises(ValueError, match="credentials"):
        RepoSnapshotConfig(repositories={"bad": {"url": "https://token@gitlab.example.com/group/repo.git"}})


def test_relative_cache_root_resolves_under_runtime_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "runtime-home"))

    assert RepoSnapshotConfig(cache_root="git-cache").resolved_cache_root() == tmp_path / "runtime-home" / "git-cache"


def test_snapshot_paths_are_isolated_by_user_and_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(repo_snapshot_tool, "get_paths", lambda: Paths(tmp_path / "runtime"))

    first = repo_snapshot_tool._snapshot_dir(_runtime(config, user_id="alice", thread_id="one"), "frontend")
    second = repo_snapshot_tool._snapshot_dir(_runtime(config, user_id="bob", thread_id="one"), "frontend")
    third = repo_snapshot_tool._snapshot_dir(_runtime(config, user_id="alice", thread_id="two"), "frontend")

    assert len({first, second, third}) == 3
    assert first == tmp_path / "runtime" / "users" / "alice" / "threads" / "one" / "user-data" / "workspace" / "repos" / "frontend"


@pytest.mark.asyncio
async def test_prepare_uses_mirror_and_creates_self_contained_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    runtime = _runtime(config)
    monkeypatch.setattr(repo_snapshot_tool, "get_paths", lambda: Paths(tmp_path / "runtime"))
    calls: list[list[str]] = []

    async def fake_git(_config: RepoSnapshotConfig, argv: list[str], **_: object) -> repo_snapshot_tool._GitResult:
        calls.append(argv)
        if "rev-parse" in argv:
            return repo_snapshot_tool._GitResult(0, "a" * 40 + "\n", "")
        if argv[:2] == ["clone", "--mirror"]:
            Path(argv[-1]).mkdir(parents=True)
        if argv[:2] == ["clone", "--no-checkout"]:
            Path(argv[-1]).mkdir(parents=True)
        return repo_snapshot_tool._GitResult(0, "", "")

    monkeypatch.setattr(repo_snapshot_tool, "_run_git", fake_git)

    result = json.loads(await repo_snapshot_tool.repo_prepare_tool.coroutine(runtime, "frontend"))

    assert result["ok"] is True
    assert result["commit_sha"] == "a" * 40
    assert result["workspace_path"] == "/mnt/user-data/workspace/repos/frontend"
    clone = next(argv for argv in calls if argv[:2] == ["clone", "--no-checkout"])
    assert "--reference-if-able" in clone
    assert "--dissociate" in clone
    assert any(argv[:3] == ["-C", str(tmp_path / "runtime" / "users" / "user-a" / "threads" / "thread-a" / "user-data" / "workspace" / "repos" / "frontend.tmp"), "checkout"] for argv in calls)
    manifest = json.loads((tmp_path / "runtime" / "users" / "user-a" / "threads" / "thread-a" / "user-data" / "workspace" / "repos" / "frontend" / ".deerflow-repo-snapshot.json").read_text())
    assert manifest["commit_sha"] == "a" * 40
    assert "token" not in json.dumps(manifest).lower()


@pytest.mark.asyncio
async def test_prepare_reuses_matching_snapshot_without_fetch_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    runtime = _runtime(config)
    monkeypatch.setattr(repo_snapshot_tool, "get_paths", lambda: Paths(tmp_path / "runtime"))
    snapshot = repo_snapshot_tool._snapshot_dir(runtime, "frontend")
    snapshot.mkdir(parents=True)
    (snapshot / ".deerflow-repo-snapshot.json").write_text(
        json.dumps({"repository": "frontend", "commit_sha": "b" * 40, "ref": "main"}),
        encoding="utf-8",
    )

    async def fail_git(*_: object, **__: object) -> repo_snapshot_tool._GitResult:
        raise AssertionError("git must not run when a matching snapshot is reusable")

    monkeypatch.setattr(repo_snapshot_tool, "_run_git", fail_git)

    result = json.loads(await repo_snapshot_tool.repo_prepare_tool.coroutine(runtime, "frontend"))

    assert result["ok"] is True
    assert result["reused"] is True
    assert result["commit_sha"] == "b" * 40


@pytest.mark.asyncio
async def test_prepare_rejects_unknown_repository_before_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)

    async def fail_git(*_: object, **__: object) -> repo_snapshot_tool._GitResult:
        raise AssertionError("git must not run for an unknown repository")

    monkeypatch.setattr(repo_snapshot_tool, "_run_git", fail_git)
    result = json.loads(await repo_snapshot_tool.repo_prepare_tool.coroutine(_runtime(config), "untrusted"))

    assert result["ok"] is False
    assert "not configured" in result["error"]


@pytest.mark.asyncio
async def test_git_runner_uses_exec_without_shell_and_masks_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path, token="super-secret")
    observed: dict[str, object] = {}

    class Process:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"fatal: super-secret was rejected"

        def kill(self) -> None:
            pass

    async def fake_exec(*argv: str, **kwargs: object) -> Process:
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(repo_snapshot_tool.asyncio, "create_subprocess_exec", fake_exec)
    result = await repo_snapshot_tool._run_git(config, ["fetch", "origin"], cwd=tmp_path)

    assert observed["argv"] == ("git", "fetch", "origin")
    assert "shell" not in observed["kwargs"]
    assert "super-secret" not in result.stderr
    assert "***" in result.stderr
