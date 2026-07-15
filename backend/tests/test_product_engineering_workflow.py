from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from deerflow.mcp.product_engineering_workflow import (
    PipelineStore,
    advance_pipeline,
    parse_agent_result,
    start_pipeline,
)


def _agent_output(agent: str, status: str = "ready") -> str:
    next_agent = {
        "product-agent": "tech-agent",
        "tech-agent": "dev-agent",
        "dev-agent": "human-review",
    }[agent]
    gate = {
        "product-agent": "prd_approval_required",
        "tech-agent": "tech_design_approval_required",
        "dev-agent": "code_review_required",
    }[agent]
    artifact = {
        "product-agent": "product-specs/test/example/prd.md",
        "tech-agent": "tech-specs/test/example/tech-design.md",
        "dev-agent": "implementation-results/test/example/implementation-report.md",
    }[agent]
    return f"""Finished.

```yaml
AGENT_RESULT:
  agent: {agent}
  status: {status}
  artifacts:
    - {artifact}
  blockers: []
  next_agent_reminder:
    next_agent: {next_agent}
    gate: {gate}
    status: {status}
    required_inputs:
      - {artifact}
    suggested_prompt: Continue the workflow.
```
"""


def _execution(agent: str, status: str = "ready") -> dict:
    return {
        "agent": agent,
        "status": "completed",
        "result": _agent_output(agent, status),
        "error": None,
        "thread_id": "thread-1",
    }


def test_parse_agent_result_enforces_expected_handoff():
    parsed = parse_agent_result(_agent_output("product-agent"), expected_agent="product-agent")

    assert parsed["status"] == "ready"
    assert parsed["next_agent_reminder"]["next_agent"] == "tech-agent"

    with pytest.raises(ValueError, match="expected agent 'product-agent'"):
        parse_agent_result(_agent_output("tech-agent"), expected_agent="product-agent")


def test_parse_agent_result_rejects_missing_structured_contract():
    with pytest.raises(ValueError, match="AGENT_RESULT"):
        parse_agent_result("Product work is done.", expected_agent="product-agent")


def test_parse_agent_result_rejects_ready_contract_with_blockers_or_wrong_artifact_path():
    with pytest.raises(ValueError, match="blockers must be empty"):
        parse_agent_result(
            _agent_output("product-agent").replace("blockers: []", "blockers:\n    - unresolved"),
            expected_agent="product-agent",
        )

    with pytest.raises(ValueError, match="product-specs/"):
        parse_agent_result(
            _agent_output("product-agent").replace("product-specs/test/example/prd.md", "artifacts/product-agent.md"),
            expected_agent="product-agent",
        )


@pytest.mark.asyncio
async def test_start_pipeline_runs_m1_and_waits_for_prd_approval(tmp_path):
    runner = AsyncMock(return_value=_execution("product-agent"))
    store = PipelineStore(tmp_path)

    state = await start_pipeline(
        prompt="Build a returns workflow",
        user_id="alice",
        runner=runner,
        store=store,
        thread_id="thread-1",
    )

    assert state["stage"] == "awaiting_prd_approval"
    assert state["current_agent"] == "product-agent"
    assert state["stage_results"]["product-agent"]["contract"]["status"] == "ready"
    runner.assert_awaited_once()
    assert runner.await_args.args[:2] == ("product-agent", "Build a returns workflow")


@pytest.mark.asyncio
async def test_start_pipeline_accepts_client_generated_workflow_id_for_recovery(tmp_path):
    runner = AsyncMock(return_value=_execution("product-agent"))
    workflow_id = "a" * 32

    state = await start_pipeline(
        prompt="Build it",
        user_id="alice",
        runner=runner,
        store=PipelineStore(tmp_path),
        workflow_id=workflow_id,
    )

    assert state["workflow_id"] == workflow_id


@pytest.mark.asyncio
async def test_pipeline_advances_one_approved_stage_at_a_time(tmp_path):
    runner = AsyncMock(
        side_effect=[
            _execution("product-agent"),
            _execution("tech-agent"),
            _execution("dev-agent"),
        ]
    )
    store = PipelineStore(tmp_path)
    started = await start_pipeline(prompt="Build it", user_id="alice", runner=runner, store=store)

    tech = await advance_pipeline(
        workflow_id=started["workflow_id"],
        user_id="alice",
        runner=runner,
        store=store,
        prd_approved=True,
    )
    assert tech["stage"] == "awaiting_tech_design_approval"
    assert tech["current_agent"] == "tech-agent"
    assert tech["approvals"]["prd"]["actor"] == "alice"
    assert len(tech["attempts"]) == 2

    completed = await advance_pipeline(
        workflow_id=started["workflow_id"],
        user_id="alice",
        runner=runner,
        store=store,
        tech_design_approved=True,
        approval_actor="tech-lead",
        approval_note="Reviewed design v1",
        dry_run=True,
    )
    assert completed["stage"] == "awaiting_code_review"
    assert completed["current_agent"] == "dev-agent"
    assert completed["approvals"]["tech_design"]["actor"] == "tech-lead"
    assert completed["approvals"]["tech_design"]["note"] == "Reviewed design v1"
    assert len(completed["attempts"]) == 3
    assert [call.args[0] for call in runner.await_args_list] == ["product-agent", "tech-agent", "dev-agent"]


@pytest.mark.asyncio
async def test_pipeline_does_not_cross_user_boundary(tmp_path):
    runner = AsyncMock(return_value=_execution("product-agent"))
    store = PipelineStore(tmp_path)
    started = await start_pipeline(prompt="Build it", user_id="alice", runner=runner, store=store)

    with pytest.raises(FileNotFoundError, match="workflow"):
        await advance_pipeline(
            workflow_id=started["workflow_id"],
            user_id="bob",
            runner=runner,
            store=store,
            prd_approved=True,
        )


@pytest.mark.asyncio
async def test_pipeline_requires_matching_approval(tmp_path):
    runner = AsyncMock(return_value=_execution("product-agent"))
    store = PipelineStore(tmp_path)
    started = await start_pipeline(prompt="Build it", user_id="alice", runner=runner, store=store)

    with pytest.raises(ValueError, match="prd_approved=true"):
        await advance_pipeline(
            workflow_id=started["workflow_id"],
            user_id="alice",
            runner=runner,
            store=store,
        )


@pytest.mark.asyncio
async def test_invalid_agent_contract_blocks_pipeline_instead_of_advancing(tmp_path):
    runner = AsyncMock(
        return_value={
            "agent": "product-agent",
            "status": "completed",
            "result": "Done without a contract",
            "error": None,
        }
    )

    state = await start_pipeline(
        prompt="Build it",
        user_id="alice",
        runner=runner,
        store=PipelineStore(tmp_path),
    )

    assert state["stage"] == "blocked"
    assert "AGENT_RESULT" in state["last_error"]


@pytest.mark.asyncio
async def test_concurrent_advance_only_runs_next_stage_once(tmp_path):
    calls: list[str] = []

    async def runner(agent: str, _prompt: str, **_kwargs):
        calls.append(agent)
        if agent == "tech-agent":
            await asyncio.sleep(0.02)
        return _execution(agent)

    store = PipelineStore(tmp_path)
    started = await start_pipeline(prompt="Build it", user_id="alice", runner=runner, store=store)
    results = await asyncio.gather(
        advance_pipeline(
            workflow_id=started["workflow_id"],
            user_id="alice",
            runner=runner,
            store=store,
            prd_approved=True,
        ),
        advance_pipeline(
            workflow_id=started["workflow_id"],
            user_id="alice",
            runner=runner,
            store=store,
            prd_approved=True,
        ),
        return_exceptions=True,
    )

    assert calls.count("tech-agent") == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
