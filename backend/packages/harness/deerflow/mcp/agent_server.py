"""MCP server exposing DeerFlow product-engineering subagents."""

from __future__ import annotations

import argparse
import logging
import os
import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP

from deerflow.config import get_app_config
from deerflow.config.runtime_paths import runtime_home
from deerflow.mcp.product_engineering_workflow import (
    PipelineStore,
    advance_pipeline,
    get_pipeline_status,
    start_pipeline,
)
from deerflow.subagents import SubagentExecutor, get_subagent_config
from deerflow.subagents.config import resolve_subagent_model_name
from deerflow.tools import get_available_tools

logger = logging.getLogger(__name__)

PRODUCT_AGENT = "product-agent"
TECH_AGENT = "tech-agent"
DEV_AGENT = "dev-agent"
EXPOSED_PRODUCT_ENGINEERING_AGENTS = (PRODUCT_AGENT, TECH_AGENT, DEV_AGENT)


def _approval_error(agent_name: str, gate: str) -> ValueError:
    return ValueError(f"{agent_name} requires {gate}=true before it can be run through the external MCP server.")


def _validate_external_gate(
    agent_name: str,
    *,
    prd_approved: bool,
    tech_design_approved: bool,
    dry_run: bool,
    allowed_paths: list[str] | None,
) -> None:
    """Enforce product-engineering gates at the MCP boundary."""
    if agent_name == TECH_AGENT and not prd_approved:
        raise _approval_error(agent_name, "prd_approved")
    if agent_name == DEV_AGENT:
        if not prd_approved:
            raise _approval_error(agent_name, "prd_approved")
        if not tech_design_approved:
            raise _approval_error(agent_name, "tech_design_approved")
        if not dry_run and not allowed_paths:
            raise ValueError("dev-agent requires allowed_paths when dry_run=false.")


def _external_guardrail_prompt(
    agent_name: str,
    prompt: str,
    *,
    prd_approved: bool,
    tech_design_approved: bool,
    dry_run: bool,
    allowed_paths: list[str] | None,
) -> str:
    allowed = "\n".join(f"- {path}" for path in (allowed_paths or [])) or "- <none supplied>"
    return f"""External MCP invocation guardrails:
- agent: {agent_name}
- prd_approved: {prd_approved}
- tech_design_approved: {tech_design_approved}
- dry_run: {dry_run}
- allowed_paths:
{allowed}

Rules for this run:
- Treat the approval flags above as authoritative runtime gates.
- If dry_run is true, do not modify files; produce the intended artifacts, plan, blockers, and commands only in the final response.
- If allowed_paths is non-empty, do not write outside those paths.
- If a required approval or allowed path is missing for the requested action, stop with AGENT_RESULT.status=blocked.

User task:
{prompt}"""


def _result_payload(result: Any) -> dict[str, Any]:
    return {
        "task_id": result.task_id,
        "trace_id": result.trace_id,
        "status": result.status.value,
        "result": result.result,
        "error": result.error,
        "started_at": result.started_at.isoformat() if result.started_at else None,
        "completed_at": result.completed_at.isoformat() if result.completed_at else None,
        "token_usage_records": result.token_usage_records,
    }


async def run_product_engineering_agent(
    agent_name: str,
    prompt: str,
    *,
    thread_id: str | None = None,
    user_id: str = "mcp",
    model_name: str | None = None,
    trace_id: str | None = None,
    prd_approved: bool = False,
    tech_design_approved: bool = False,
    dry_run: bool = True,
    allowed_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Run one configured product-engineering subagent and return its final payload."""
    if agent_name not in EXPOSED_PRODUCT_ENGINEERING_AGENTS:
        allowed = ", ".join(EXPOSED_PRODUCT_ENGINEERING_AGENTS)
        raise ValueError(f"Unsupported product-engineering agent '{agent_name}'. Allowed: {allowed}")
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    _validate_external_gate(
        agent_name,
        prd_approved=prd_approved,
        tech_design_approved=tech_design_approved,
        dry_run=dry_run,
        allowed_paths=allowed_paths,
    )

    app_config = get_app_config()
    config = get_subagent_config(agent_name, app_config=app_config)
    if config is None:
        raise ValueError(f"Subagent '{agent_name}' is not configured. Define it under subagents.custom_agents in config.yaml.")

    parent_model = model_name
    effective_model = resolve_subagent_model_name(config, parent_model, app_config=app_config)
    tools = get_available_tools(
        model_name=effective_model,
        subagent_enabled=False,
        app_config=app_config,
    )
    run_thread_id = thread_id or f"mcp-{agent_name}-{uuid.uuid4().hex[:8]}"
    executor = SubagentExecutor(
        config=config,
        tools=tools,
        app_config=app_config,
        parent_model=parent_model,
        thread_id=run_thread_id,
        trace_id=trace_id or uuid.uuid4().hex[:8],
        user_id=user_id,
    )

    guarded_prompt = _external_guardrail_prompt(
        agent_name,
        prompt,
        prd_approved=prd_approved,
        tech_design_approved=tech_design_approved,
        dry_run=dry_run,
        allowed_paths=allowed_paths,
    )
    result = await executor._aexecute(guarded_prompt)
    payload = _result_payload(result)
    payload["agent"] = agent_name
    payload["thread_id"] = run_thread_id
    payload["model_name"] = effective_model
    payload["dry_run"] = dry_run
    payload["allowed_paths"] = allowed_paths or []
    return payload


def create_agent_mcp_server(*, host: str = "127.0.0.1", port: int = 8003) -> FastMCP:
    """Create the MCP server that exposes M1/M2/M3 as external MCP tools."""
    server = FastMCP(
        "DeerFlow Product Engineering Agents",
        instructions=(
            "Runs DeerFlow's configured M1 product-agent, M2 tech-agent, and "
            "M3 dev-agent as external MCP tools. These tools execute the real "
            "subagents from config.yaml and preserve their configured skills, "
            "tool policy, model selection, and timeout limits."
        ),
        host=host,
        port=port,
    )
    pipeline_store = PipelineStore(runtime_home() / "product-engineering-pipelines")

    @server.tool(
        name="start_product_engineering_pipeline",
        description="Start a durable M1 -> M2 -> M3 workflow, run M1, and stop at the PRD approval gate.",
    )
    async def start_product_engineering_pipeline(
        prompt: str,
        user_id: str = "mcp",
        workflow_id: str | None = None,
        thread_id: str | None = None,
        model_name: str | None = None,
        dev_dry_run: bool = True,
        allowed_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        return await start_pipeline(
            prompt=prompt,
            user_id=user_id,
            runner=run_product_engineering_agent,
            store=pipeline_store,
            thread_id=thread_id,
            model_name=model_name,
            dev_dry_run=dev_dry_run,
            allowed_paths=allowed_paths,
            workflow_id=workflow_id,
        )

    @server.tool(
        name="advance_product_engineering_pipeline",
        description="Advance one durable workflow stage after approval, or retry the current stage with additional input.",
    )
    async def advance_product_engineering_pipeline(
        workflow_id: str,
        user_id: str = "mcp",
        prd_approved: bool = False,
        tech_design_approved: bool = False,
        continuation_prompt: str | None = None,
        approval_actor: str | None = None,
        approval_note: str | None = None,
        dry_run: bool | None = None,
        allowed_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        return await advance_pipeline(
            workflow_id=workflow_id,
            user_id=user_id,
            runner=run_product_engineering_agent,
            store=pipeline_store,
            prd_approved=prd_approved,
            tech_design_approved=tech_design_approved,
            continuation_prompt=continuation_prompt,
            approval_actor=approval_actor,
            approval_note=approval_note,
            dry_run=dry_run,
            allowed_paths=allowed_paths,
        )

    @server.tool(
        name="get_product_engineering_pipeline_status",
        description="Get a durable M1/M2/M3 workflow state for the owning DeerFlow user.",
    )
    async def get_product_engineering_pipeline_status(
        workflow_id: str,
        user_id: str = "mcp",
    ) -> dict[str, Any]:
        return await get_pipeline_status(workflow_id=workflow_id, user_id=user_id, store=pipeline_store)

    @server.tool(
        name="run_product_agent",
        description="Run M1 Product Agent to convert raw requirements or Q&A into PRD artifacts.",
    )
    async def run_product_agent(
        prompt: str,
        thread_id: str | None = None,
        user_id: str = "mcp",
        model_name: str | None = None,
        dry_run: bool = False,
        allowed_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        return await run_product_engineering_agent(
            PRODUCT_AGENT,
            prompt,
            thread_id=thread_id,
            user_id=user_id,
            model_name=model_name,
            dry_run=dry_run,
            allowed_paths=allowed_paths,
        )

    @server.tool(
        name="run_tech_agent",
        description="Run M2 Tech Agent to turn an approved PRD into technical design, implementation plan, tests, and risks.",
    )
    async def run_tech_agent(
        prompt: str,
        prd_approved: bool,
        thread_id: str | None = None,
        user_id: str = "mcp",
        model_name: str | None = None,
        dry_run: bool = False,
        allowed_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        return await run_product_engineering_agent(
            TECH_AGENT,
            prompt,
            thread_id=thread_id,
            user_id=user_id,
            model_name=model_name,
            prd_approved=prd_approved,
            dry_run=dry_run,
            allowed_paths=allowed_paths,
        )

    @server.tool(
        name="run_dev_agent",
        description="Run M3 Dev Agent to implement an approved PRD and technical plan, then report implementation and test artifacts.",
    )
    async def run_dev_agent(
        prompt: str,
        prd_approved: bool,
        tech_design_approved: bool,
        thread_id: str | None = None,
        user_id: str = "mcp",
        model_name: str | None = None,
        dry_run: bool = True,
        allowed_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        return await run_product_engineering_agent(
            DEV_AGENT,
            prompt,
            thread_id=thread_id,
            user_id=user_id,
            model_name=model_name,
            prd_approved=prd_approved,
            tech_design_approved=tech_design_approved,
            dry_run=dry_run,
            allowed_paths=allowed_paths,
        )

    return server


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Expose DeerFlow M1/M2/M3 product-engineering agents as an MCP server.")
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default=os.getenv("DEER_FLOW_AGENT_MCP_TRANSPORT", "streamable-http"),
        help="MCP transport to serve. Use streamable-http or sse for external network clients.",
    )
    parser.add_argument("--host", default=os.getenv("DEER_FLOW_AGENT_MCP_HOST", "127.0.0.1"), help="Host for HTTP/SSE transports.")
    parser.add_argument("--port", type=int, default=int(os.getenv("DEER_FLOW_AGENT_MCP_PORT", "8003")), help="Port for HTTP/SSE transports.")
    parser.add_argument("--config", default=os.getenv("DEER_FLOW_CONFIG_PATH"), help="Path to config.yaml. Defaults to DeerFlow config resolution.")
    parser.add_argument("--project-root", default=os.getenv("DEER_FLOW_PROJECT_ROOT"), help="Project root containing config.yaml and extensions_config.json.")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.config:
        os.environ["DEER_FLOW_CONFIG_PATH"] = args.config
    if args.project_root:
        os.environ["DEER_FLOW_PROJECT_ROOT"] = args.project_root

    logging.basicConfig(level=logging.INFO)
    server = create_agent_mcp_server(host=args.host, port=args.port)
    transport = args.transport
    logger.info("Starting DeerFlow agent MCP server on transport=%s host=%s port=%s", transport, args.host, args.port)
    server.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
