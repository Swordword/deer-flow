"""Controlled Git mirror and per-thread repository snapshot tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.tools import tool

from deerflow.config import get_app_config
from deerflow.config.paths import get_paths
from deerflow.config.repo_snapshot_config import RepositoryConfig, RepoSnapshotConfig
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime

_MANIFEST_NAME = ".deerflow-repo-snapshot.json"
_VIRTUAL_REPOS_ROOT = "/mnt/user-data/workspace/repos"


@dataclass(frozen=True)
class _GitResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class _GitCommandError(RuntimeError):
    pass


class _FileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+")
        try:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - Windows is not a supported Docker host runtime.
            pass

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except ImportError:  # pragma: no cover
            pass
        self._handle.close()
        self._handle = None


def _runtime_app_config(runtime: Runtime | None) -> Any:
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        app_config = context.get("app_config")
        if app_config is not None:
            return app_config
    return get_app_config()


def _repo_config(runtime: Runtime | None) -> RepoSnapshotConfig:
    return _runtime_app_config(runtime).repo_snapshot


def _response(**payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _thread_id(runtime: Runtime) -> str | None:
    context = getattr(runtime, "context", None)
    if isinstance(context, dict) and context.get("thread_id"):
        return str(context["thread_id"])
    config = getattr(runtime, "config", None)
    if isinstance(config, dict):
        value = (config.get("configurable") or {}).get("thread_id")
        if value:
            return str(value)
    return None


def _workspace_name(alias: str, repository: RepositoryConfig) -> str:
    return repository.workspace_name or alias


def _snapshot_dir(runtime: Runtime, alias: str) -> Path:
    thread_id = _thread_id(runtime)
    if not thread_id:
        raise ValueError("repo_prepare requires a thread_id")
    config = _repo_config(runtime)
    repository = config.repositories.get(alias)
    if repository is None:
        raise ValueError(f"Repository alias {alias!r} is not configured")
    user_id = resolve_runtime_user_id(runtime)
    return get_paths().sandbox_work_dir(thread_id, user_id=user_id) / "repos" / _workspace_name(alias, repository)


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def _mask(text: str, config: RepoSnapshotConfig) -> str:
    return text.replace(config.token, "***") if config.token else text


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n...<truncated>...\n{text[-half:]}"


def _ensure_askpass(config: RepoSnapshotConfig) -> Path:
    path = config.resolved_cache_root() / ".git-askpass.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = """#!/bin/sh
case "$1" in
  *Username*) printf '%s\\n' "${DEERFLOW_GIT_USERNAME:-oauth2}" ;;
  *) printf '%s\\n' "$DEERFLOW_GIT_TOKEN" ;;
esac
"""
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o700)
    return path


def _git_env(config: RepoSnapshotConfig) -> dict[str, str]:
    env = os.environ.copy()
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"})
    if config.token:
        env.update(
            {
                "GIT_ASKPASS": str(_ensure_askpass(config)),
                "DEERFLOW_GIT_USERNAME": config.username,
                "DEERFLOW_GIT_TOKEN": config.token,
            }
        )
    return env


async def _run_git(
    config: RepoSnapshotConfig,
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: float | None = None,
) -> _GitResult:
    env = await asyncio.to_thread(_git_env, config)
    process = await asyncio.create_subprocess_exec(
        config.git_executable,
        *argv,
        cwd=str(cwd) if cwd else None,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds or config.command_timeout_seconds,
        )
    except TimeoutError:
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()
        return _GitResult(
            process.returncode if process.returncode is not None else -9,
            _mask(stdout_bytes.decode("utf-8", errors="replace"), config),
            _mask(stderr_bytes.decode("utf-8", errors="replace"), config),
            timed_out=True,
        )
    return _GitResult(
        process.returncode if process.returncode is not None else 0,
        _mask(stdout_bytes.decode("utf-8", errors="replace"), config),
        _mask(stderr_bytes.decode("utf-8", errors="replace"), config),
    )


async def _checked_git(config: RepoSnapshotConfig, argv: list[str], *, cwd: Path | None = None) -> str:
    result = await _run_git(config, argv, cwd=cwd)
    if result.returncode != 0 or result.timed_out:
        detail = _truncate(result.stderr or result.stdout, config.output_max_chars).strip()
        raise _GitCommandError(f"git {' '.join(argv[:3])} failed: {detail or 'unknown error'}")
    return result.stdout


def _read_manifest(snapshot: Path) -> dict[str, Any] | None:
    path = snapshot / _MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_manifest(snapshot: Path, payload: dict[str, Any]) -> None:
    path = snapshot / _MANIFEST_NAME
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _prepare_temp_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.rmtree(path)


def _replace_snapshot(temp: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    os.replace(temp, target)


async def _ensure_mirror(config: RepoSnapshotConfig, repository: RepositoryConfig, mirror: Path) -> None:
    if mirror.is_dir():
        await _checked_git(config, ["-C", str(mirror), "remote", "set-url", "origin", repository.url])
        await _checked_git(config, ["-C", str(mirror), "fetch", "--prune", "--tags", "origin"])
        return
    await asyncio.to_thread(mirror.parent.mkdir, parents=True, exist_ok=True)
    await _checked_git(config, ["clone", "--mirror", repository.url, str(mirror)])


async def _resolve_commit(config: RepoSnapshotConfig, mirror: Path, ref: str) -> str:
    output = await _checked_git(config, ["-C", str(mirror), "rev-parse", "--verify", f"{ref}^{{commit}}"])
    commit_sha = output.strip().splitlines()[-1] if output.strip() else ""
    if len(commit_sha) != 40 or any(ch not in "0123456789abcdefABCDEF" for ch in commit_sha):
        raise _GitCommandError(f"Git resolved {ref!r} to an invalid commit SHA")
    return commit_sha.lower()


def _validate_ref(ref: str) -> str:
    ref = ref.strip()
    if not ref or ref.startswith("-") or any(ch in ref for ch in "\x00\n\r"):
        raise ValueError("ref is invalid")
    return ref


def _snapshot_payload(runtime: Runtime, alias: str, repository: RepositoryConfig, manifest: dict[str, Any], *, reused: bool) -> str:
    workspace_name = _workspace_name(alias, repository)
    return _response(
        ok=True,
        repository=alias,
        ref=manifest["ref"],
        commit_sha=manifest["commit_sha"],
        workspace_path=f"{_VIRTUAL_REPOS_ROOT}/{workspace_name}",
        manifest_path=f"{_VIRTUAL_REPOS_ROOT}/{workspace_name}/{_MANIFEST_NAME}",
        reused=reused,
        message="Use local grep/read_file/bash in workspace_path. GitLab MCP is only a metadata or missing-source fallback.",
    )


@tool("repo_prepare", parse_docstring=True)
async def repo_prepare_tool(runtime: Runtime, repository: str, ref: str | None = None, refresh: bool = False) -> str:
    """Prepare an approved Git repository as a persistent, self-contained thread snapshot.

    Use this once before code analysis. Reuse its workspace_path and commit_sha in
    later M2/M3 stages instead of reading source files through GitLab MCP.

    Args:
        repository: Administrator-configured repository alias.
        ref: Optional branch, tag, or commit. Defaults to the configured ref.
        refresh: Re-fetch and replace an existing clean snapshot. Leave false during a workflow.
    """
    config = _repo_config(runtime)
    if not config.enabled:
        return _response(ok=False, error="repo_snapshot is disabled")
    repo = config.repositories.get(repository)
    if repo is None:
        return _response(ok=False, error=f"Repository alias {repository!r} is not configured", available=sorted(config.repositories))
    try:
        selected_ref = _validate_ref(ref or repo.default_ref)
        snapshot = _snapshot_dir(runtime, repository)
    except ValueError as exc:
        return _response(ok=False, error=str(exc))

    manifest = await asyncio.to_thread(_read_manifest, snapshot)
    if manifest and not refresh and manifest.get("repository") == repository and manifest.get("ref") == selected_ref:
        return _snapshot_payload(runtime, repository, repo, manifest, reused=True)

    cache_key = _cache_key(repo.url)
    mirror = config.resolved_cache_root() / "mirrors" / f"{cache_key}.git"
    lock = _FileLock(config.resolved_cache_root() / "locks" / f"{cache_key}.lock")
    temp = snapshot.with_name(f"{snapshot.name}.tmp")
    await asyncio.to_thread(lock.acquire)
    try:
        if snapshot.exists() and refresh:
            status = await _checked_git(config, ["-C", str(snapshot), "status", "--porcelain"])
            if status.strip():
                return _response(ok=False, error="Existing snapshot has local changes; refusing refresh", workspace_path=f"{_VIRTUAL_REPOS_ROOT}/{_workspace_name(repository, repo)}")
        await _ensure_mirror(config, repo, mirror)
        commit_sha = await _resolve_commit(config, mirror, selected_ref)
        await asyncio.to_thread(_prepare_temp_dir, temp)
        await _checked_git(
            config,
            [
                "clone",
                "--no-checkout",
                "--reference-if-able",
                str(mirror),
                "--dissociate",
                str(mirror),
                str(temp),
            ],
        )
        await _checked_git(config, ["-C", str(temp), "checkout", "--detach", commit_sha])
        await _checked_git(config, ["-C", str(temp), "remote", "set-url", "origin", repo.url])
        payload = {
            "repository": repository,
            "repository_url": repo.url,
            "ref": selected_ref,
            "commit_sha": commit_sha,
            "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        await asyncio.to_thread(_write_manifest, temp, payload)
        await asyncio.to_thread(_replace_snapshot, temp, snapshot)
        return _snapshot_payload(runtime, repository, repo, payload, reused=False)
    except (OSError, _GitCommandError) as exc:
        await asyncio.to_thread(_prepare_temp_dir, temp)
        return _response(ok=False, error=_truncate(_mask(str(exc), config), config.output_max_chars))
    finally:
        await asyncio.to_thread(lock.release)


@tool("repo_snapshot_status", parse_docstring=True)
async def repo_snapshot_status_tool(runtime: Runtime, repository: str) -> str:
    """Return the pinned commit and local-change status of a thread repository snapshot.

    Args:
        repository: Administrator-configured repository alias.
    """
    config = _repo_config(runtime)
    if not config.enabled:
        return _response(ok=False, error="repo_snapshot is disabled")
    repo = config.repositories.get(repository)
    if repo is None:
        return _response(ok=False, error=f"Repository alias {repository!r} is not configured", available=sorted(config.repositories))
    try:
        snapshot = _snapshot_dir(runtime, repository)
    except ValueError as exc:
        return _response(ok=False, error=str(exc))
    manifest = await asyncio.to_thread(_read_manifest, snapshot)
    if manifest is None:
        return _response(ok=False, prepared=False, error="Repository snapshot is not prepared")
    result = await _run_git(config, ["-C", str(snapshot), "status", "--porcelain"])
    if result.returncode != 0:
        return _response(ok=False, prepared=True, error=_truncate(result.stderr or result.stdout, config.output_max_chars))
    return _response(
        ok=True,
        prepared=True,
        repository=repository,
        ref=manifest.get("ref"),
        commit_sha=manifest.get("commit_sha"),
        workspace_path=f"{_VIRTUAL_REPOS_ROOT}/{_workspace_name(repository, repo)}",
        dirty=bool(result.stdout.strip()),
        changed_paths=result.stdout.splitlines()[:100],
    )
