from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from deerflow.mcp import agent_server
from deerflow.subagents.config import SubagentConfig


@pytest.mark.asyncio
async def test_run_product_engineering_agent_uses_configured_subagent(monkeypatch):
    config = SubagentConfig(
        name="product-agent",
        description="Product agent",
        system_prompt="Prompt",
        tools=[],
        skills=[],
        model="inherit",
        max_turns=3,
        timeout_seconds=30,
    )
    app_config = SimpleNamespace(models=[SimpleNamespace(name="test-model")], tools=[], subagents=SimpleNamespace())
    executor_instance = Mock()
    executor_instance._aexecute = AsyncMock(
        return_value=SimpleNamespace(
            task_id="task-1",
            trace_id="trace-1",
            status=SimpleNamespace(value="completed"),
            result="done",
            error=None,
            started_at=datetime(2026, 1, 1),
            completed_at=datetime(2026, 1, 1, 0, 0, 1),
            token_usage_records=[],
        )
    )
    executor_cls = Mock(return_value=executor_instance)

    monkeypatch.setattr(agent_server, "get_app_config", Mock(return_value=app_config))
    monkeypatch.setattr(agent_server, "get_subagent_config", Mock(return_value=config))
    monkeypatch.setattr(agent_server, "get_available_tools", Mock(return_value=[]))
    monkeypatch.setattr(agent_server, "SubagentExecutor", executor_cls)

    payload = await agent_server.run_product_engineering_agent(
        "product-agent",
        "make a PRD",
        thread_id="thread-1",
        user_id="user-1",
        model_name="parent-model",
        trace_id="trace-1",
    )

    assert payload["agent"] == "product-agent"
    assert payload["status"] == "completed"
    assert payload["result"] == "done"
    assert payload["thread_id"] == "thread-1"
    assert payload["model_name"] == "parent-model"
    agent_server.get_subagent_config.assert_called_once_with("product-agent", app_config=app_config)
    agent_server.get_available_tools.assert_called_once_with(
        model_name="parent-model",
        subagent_enabled=False,
        app_config=app_config,
    )
    executor_cls.assert_called_once()
    assert executor_cls.call_args.kwargs["config"] is config
    assert executor_cls.call_args.kwargs["thread_id"] == "thread-1"
    assert executor_cls.call_args.kwargs["user_id"] == "user-1"
    executed_prompt = executor_instance._aexecute.call_args.args[0]
    assert "External MCP invocation guardrails:" in executed_prompt
    assert "dry_run: True" in executed_prompt
    assert "User task:\nmake a PRD" in executed_prompt


@pytest.mark.asyncio
async def test_run_product_engineering_agent_rejects_unknown_agent():
    with pytest.raises(ValueError, match="Unsupported product-engineering agent"):
        await agent_server.run_product_engineering_agent("general-purpose", "do work")


@pytest.mark.asyncio
async def test_run_tech_agent_requires_prd_approval():
    with pytest.raises(ValueError, match="prd_approved=true"):
        await agent_server.run_product_engineering_agent("tech-agent", "write a tech spec")


@pytest.mark.asyncio
async def test_run_dev_agent_requires_prd_and_tech_approval():
    with pytest.raises(ValueError, match="prd_approved=true"):
        await agent_server.run_product_engineering_agent("dev-agent", "implement it")

    with pytest.raises(ValueError, match="tech_design_approved=true"):
        await agent_server.run_product_engineering_agent(
            "dev-agent",
            "implement it",
            prd_approved=True,
        )


@pytest.mark.asyncio
async def test_run_dev_agent_requires_allowed_paths_when_not_dry_run():
    with pytest.raises(ValueError, match="requires allowed_paths"):
        await agent_server.run_product_engineering_agent(
            "dev-agent",
            "implement it",
            prd_approved=True,
            tech_design_approved=True,
            dry_run=False,
        )


def test_create_agent_mcp_server_exposes_pipeline_and_stage_tools():
    server = agent_server.create_agent_mcp_server()

    assert set(server._tool_manager._tools) == {
        "start_product_engineering_pipeline",
        "advance_product_engineering_pipeline",
        "get_product_engineering_pipeline_status",
        "run_product_agent",
        "run_tech_agent",
        "run_dev_agent",
    }
