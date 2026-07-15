"""Durable orchestration for the M1 -> M2 -> M3 product workflow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

PRODUCT_AGENT = "product-agent"
TECH_AGENT = "tech-agent"
DEV_AGENT = "dev-agent"

_EXPECTED_HANDOFF = {
    PRODUCT_AGENT: (TECH_AGENT, "prd_approval_required"),
    TECH_AGENT: (DEV_AGENT, "tech_design_approval_required"),
    DEV_AGENT: ("human-review", "code_review_required"),
}
_READY_STAGE = {
    PRODUCT_AGENT: "awaiting_prd_approval",
    TECH_AGENT: "awaiting_tech_design_approval",
    DEV_AGENT: "awaiting_code_review",
}
_VALID_CONTRACT_STATUSES = {"ready", "needs_input", "blocked"}
_ARTIFACT_ROOT = {
    PRODUCT_AGENT: "product-specs/",
    TECH_AGENT: "tech-specs/",
    DEV_AGENT: "implementation-results/",
}
_WORKFLOW_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_FENCED_BLOCK_RE = re.compile(r"```(?:yaml|yml)?\s*\n(?P<body>.*?)```", re.IGNORECASE | re.DOTALL)

PipelineRunner = Callable[..., Awaitable[dict[str, Any]]]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def parse_agent_result(text: str, *, expected_agent: str) -> dict[str, Any]:
    """Parse and validate the final fenced ``AGENT_RESULT`` contract."""
    candidate: Any = None
    for match in _FENCED_BLOCK_RE.finditer(text or ""):
        try:
            document = yaml.safe_load(match.group("body"))
        except yaml.YAMLError:
            continue
        if isinstance(document, dict) and isinstance(document.get("AGENT_RESULT"), dict):
            candidate = document["AGENT_RESULT"]

    if not isinstance(candidate, dict):
        raise ValueError("Agent response is missing a valid fenced yaml AGENT_RESULT contract.")
    if candidate.get("agent") != expected_agent:
        raise ValueError(f"AGENT_RESULT expected agent '{expected_agent}', got {candidate.get('agent')!r}.")

    status = candidate.get("status")
    if status not in _VALID_CONTRACT_STATUSES:
        raise ValueError(f"AGENT_RESULT has invalid status {status!r}.")
    if not isinstance(candidate.get("artifacts"), list):
        raise ValueError("AGENT_RESULT.artifacts must be a list.")
    if not isinstance(candidate.get("blockers"), list):
        raise ValueError("AGENT_RESULT.blockers must be a list.")
    if status == "ready" and candidate["blockers"]:
        raise ValueError("AGENT_RESULT.blockers must be empty when status is ready.")
    artifact_root = _ARTIFACT_ROOT[expected_agent]
    artifacts = candidate["artifacts"]
    if status == "ready" and not artifacts:
        raise ValueError("AGENT_RESULT.artifacts must not be empty when status is ready.")
    for artifact in artifacts:
        if not isinstance(artifact, str) or not artifact.startswith(artifact_root) or ".." in Path(artifact).parts:
            raise ValueError(f"AGENT_RESULT artifacts for {expected_agent} must stay under {artifact_root}.")

    reminder = candidate.get("next_agent_reminder")
    if not isinstance(reminder, dict):
        raise ValueError("AGENT_RESULT.next_agent_reminder must be an object.")
    expected_next, expected_gate = _EXPECTED_HANDOFF[expected_agent]
    if reminder.get("next_agent") != expected_next:
        raise ValueError(f"AGENT_RESULT for {expected_agent} must hand off to {expected_next}.")
    if reminder.get("gate") != expected_gate:
        raise ValueError(f"AGENT_RESULT for {expected_agent} must use gate {expected_gate}.")
    if reminder.get("status") != status:
        raise ValueError("AGENT_RESULT and next_agent_reminder statuses must match.")
    if not isinstance(reminder.get("required_inputs"), list):
        raise ValueError("AGENT_RESULT.next_agent_reminder.required_inputs must be a list.")
    return candidate


class PipelineStore:
    """Atomic JSON storage partitioned by a one-way hash of the DeerFlow user id."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _owner_bucket(user_id: str) -> str:
        if not user_id.strip():
            raise ValueError("user_id must not be empty")
        return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32]

    def _path(self, user_id: str, workflow_id: str) -> Path:
        if not _WORKFLOW_ID_RE.fullmatch(workflow_id):
            raise ValueError("workflow_id is invalid")
        return self.root / self._owner_bucket(user_id) / f"{workflow_id}.json"

    def save(self, user_id: str, state: dict[str, Any]) -> None:
        path = self._path(user_id, str(state["workflow_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            json.dump(state, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def load(self, user_id: str, workflow_id: str) -> dict[str, Any]:
        path = self._path(user_id, workflow_id)
        if not path.is_file():
            raise FileNotFoundError(f"Product-engineering workflow '{workflow_id}' was not found for this user.")
        return json.loads(path.read_text(encoding="utf-8"))

    def lock(self, user_id: str, workflow_id: str) -> asyncio.Lock:
        key = f"{self._owner_bucket(user_id)}:{workflow_id}"
        return self._locks.setdefault(key, asyncio.Lock())


def _handoff_prompt(state: dict[str, Any], next_agent: str) -> str:
    previous_agent = str(state["current_agent"])
    previous = state["stage_results"][previous_agent]["contract"]
    return "\n".join(
        [
            f"Continue product-engineering workflow {state['workflow_id']} with {next_agent}.",
            "Original task:",
            str(state["prompt"]),
            f"Approved handoff from {previous_agent}:",
            json.dumps(previous, ensure_ascii=False, indent=2),
            "Read every required input from the handoff before producing the next stage.",
        ]
    )


def _approval_granted(value: Any) -> bool:
    """Read both current audit records and legacy boolean workflow state."""
    if isinstance(value, dict):
        return value.get("approved") is True
    return value is True


def _record_execution(state: dict[str, Any], agent: str, execution: dict[str, Any]) -> None:
    entry: dict[str, Any] = {"agent": agent, "execution": execution, "contract": None, "recorded_at": _utc_now()}
    state["current_agent"] = agent
    state["stage_results"][agent] = entry
    state.setdefault("attempts", []).append(entry)
    state["updated_at"] = _utc_now()

    if execution.get("status") != "completed":
        state["stage"] = "blocked"
        state["last_error"] = execution.get("error") or f"{agent} execution ended with status {execution.get('status')!r}."
        return
    try:
        contract = parse_agent_result(str(execution.get("result") or ""), expected_agent=agent)
    except ValueError as exc:
        state["stage"] = "blocked"
        state["last_error"] = str(exc)
        return

    entry["contract"] = contract
    state["last_error"] = None
    if contract["status"] == "ready":
        state["stage"] = _READY_STAGE[agent]
    else:
        state["stage"] = contract["status"]


async def start_pipeline(
    *,
    prompt: str,
    user_id: str,
    runner: PipelineRunner,
    store: PipelineStore,
    thread_id: str | None = None,
    model_name: str | None = None,
    dev_dry_run: bool = True,
    allowed_paths: list[str] | None = None,
    workflow_id: str | None = None,
) -> dict[str, Any]:
    """Create a workflow, execute M1, and stop at its explicit gate."""
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    workflow_id = workflow_id or uuid.uuid4().hex
    if not _WORKFLOW_ID_RE.fullmatch(workflow_id):
        raise ValueError("workflow_id must contain exactly 32 lowercase hexadecimal characters")
    try:
        await asyncio.to_thread(store.load, user_id, workflow_id)
    except FileNotFoundError:
        pass
    else:
        raise ValueError(f"Product-engineering workflow '{workflow_id}' already exists for this user.")
    now = _utc_now()
    state: dict[str, Any] = {
        "workflow_id": workflow_id,
        "thread_id": thread_id or f"pipeline-{workflow_id[:8]}",
        "prompt": prompt,
        "stage": "running_product",
        "current_agent": PRODUCT_AGENT,
        "model_name": model_name,
        "dev_dry_run": dev_dry_run,
        "allowed_paths": allowed_paths or [],
        "approvals": {"prd": None, "tech_design": None},
        "stage_results": {},
        "attempts": [],
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    await asyncio.to_thread(store.save, user_id, state)
    execution = await runner(
        PRODUCT_AGENT,
        prompt,
        thread_id=state["thread_id"],
        user_id=user_id,
        model_name=model_name,
        dry_run=False,
    )
    _record_execution(state, PRODUCT_AGENT, execution)
    await asyncio.to_thread(store.save, user_id, state)
    return state


async def advance_pipeline(
    *,
    workflow_id: str,
    user_id: str,
    runner: PipelineRunner,
    store: PipelineStore,
    prd_approved: bool = False,
    tech_design_approved: bool = False,
    continuation_prompt: str | None = None,
    approval_actor: str | None = None,
    approval_note: str | None = None,
    dry_run: bool | None = None,
    allowed_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Advance exactly one stage after validating the current durable gate."""
    async with store.lock(user_id, workflow_id):
        return await _advance_pipeline_locked(
            workflow_id=workflow_id,
            user_id=user_id,
            runner=runner,
            store=store,
            prd_approved=prd_approved,
            tech_design_approved=tech_design_approved,
            continuation_prompt=continuation_prompt,
            approval_actor=approval_actor,
            approval_note=approval_note,
            dry_run=dry_run,
            allowed_paths=allowed_paths,
        )


async def _advance_pipeline_locked(
    *,
    workflow_id: str,
    user_id: str,
    runner: PipelineRunner,
    store: PipelineStore,
    prd_approved: bool,
    tech_design_approved: bool,
    continuation_prompt: str | None,
    approval_actor: str | None,
    approval_note: str | None,
    dry_run: bool | None,
    allowed_paths: list[str] | None,
) -> dict[str, Any]:
    state = await asyncio.to_thread(store.load, user_id, workflow_id)
    stage = state["stage"]

    if stage == "awaiting_prd_approval":
        if not prd_approved:
            raise ValueError("This workflow requires prd_approved=true before M2 can run.")
        state["approvals"]["prd"] = {
            "approved": True,
            "actor": approval_actor or user_id,
            "note": approval_note,
            "approved_at": _utc_now(),
        }
        next_agent = TECH_AGENT
    elif stage == "awaiting_tech_design_approval":
        if not tech_design_approved:
            raise ValueError("This workflow requires tech_design_approved=true before M3 can run.")
        state["approvals"]["tech_design"] = {
            "approved": True,
            "actor": approval_actor or user_id,
            "note": approval_note,
            "approved_at": _utc_now(),
        }
        next_agent = DEV_AGENT
    elif stage in {"needs_input", "blocked"} or str(stage).startswith("running_"):
        if not continuation_prompt or not continuation_prompt.strip():
            raise ValueError(f"continuation_prompt is required to retry a workflow in stage {stage!r}.")
        next_agent = str(state["current_agent"])
    else:
        raise ValueError(f"Workflow stage {stage!r} cannot be advanced.")

    prompt = continuation_prompt.strip() if continuation_prompt else _handoff_prompt(state, next_agent)
    effective_paths = allowed_paths if allowed_paths is not None else state.get("allowed_paths", [])
    effective_dry_run = bool(state.get("dev_dry_run", True) if dry_run is None else dry_run)
    state["stage"] = f"running_{next_agent.removesuffix('-agent').replace('-', '_')}"
    state["current_agent"] = next_agent
    await asyncio.to_thread(store.save, user_id, state)

    execution = await runner(
        next_agent,
        prompt,
        thread_id=state["thread_id"],
        user_id=user_id,
        model_name=state.get("model_name"),
        prd_approved=_approval_granted(state["approvals"]["prd"]),
        tech_design_approved=_approval_granted(state["approvals"]["tech_design"]),
        dry_run=effective_dry_run if next_agent == DEV_AGENT else False,
        allowed_paths=effective_paths,
    )
    if next_agent == DEV_AGENT:
        state["dev_dry_run"] = effective_dry_run
        state["allowed_paths"] = effective_paths
    _record_execution(state, next_agent, execution)
    await asyncio.to_thread(store.save, user_id, state)
    return state


async def get_pipeline_status(*, workflow_id: str, user_id: str, store: PipelineStore) -> dict[str, Any]:
    """Return the durable state visible to the workflow owner."""
    return await asyncio.to_thread(store.load, user_id, workflow_id)
