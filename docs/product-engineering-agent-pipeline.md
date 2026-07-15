# Product Engineering Agent Pipeline

This local pipeline splits product-engineering work into three custom subagents
configured in `config.yaml`.

```text
M1 product-agent
  -> product-specs/<module>/<spec-id>/
  -> M2 tech-agent
  -> tech-specs/<module>/<spec-id>/
  -> M3 dev-agent
  -> implementation-results/<module>/<spec-id>/
  -> human review
```

## Agents

| Agent | Purpose | Skills |
| --- | --- | --- |
| `product-agent` | Converts raw requirements or Q&A into PRD artifacts. | `llm-wiki`, `prd-from-qa` |
| `tech-agent` | Converts an approved PRD into technical design, plan, tests, and risks. | `llm-wiki`, `tech-spec-from-prd` |
| `dev-agent` | Implements the approved PRD and technical plan, then records implementation and test artifacts. | `llm-wiki`, `dev-implementation` |

## Handoff Reminder

Each agent must end with a fenced yaml `AGENT_RESULT` block. The next-stage
handoff lives under `next_agent_reminder`:

```yaml
AGENT_RESULT:
  agent: product-agent
  status: ready
  artifacts:
    - product-specs/<module>/<spec-id>/prd.md
  blockers: []
  next_agent_reminder:
    next_agent: tech-agent
    gate: prd_approval_required
    status: ready
    required_inputs:
      - product-specs/<module>/<spec-id>/prd.md
    suggested_prompt: |
      Use tech-agent to generate tech-specs for <module>/<spec-id> after PRD approval.
```

The Lead Agent reads this block, summarizes the handoff to the user, and only
calls the next agent when the user explicitly confirms the relevant gate.
The external Agent MCP server parses and validates the same contract; malformed
contracts leave the durable workflow in `blocked` instead of silently advancing.

## Durable MCP Workflow

External clients should prefer these tools over manually chaining the three
stage tools:

1. Call `start_product_engineering_pipeline` to create a workflow and run M1.
   For connection-loss recovery during the first call, pre-generate and pass a
   32-character lowercase hexadecimal `workflow_id`.
2. Inspect the returned `stage` and M1 contract. After human approval, call
   `advance_product_engineering_pipeline` with `prd_approved=true` to run M2.
   Supply `approval_actor` and `approval_note` to retain useful audit context.
3. After technical-design approval, call the same tool with
   `tech_design_approved=true` to run M3.
4. Use `get_product_engineering_pipeline_status` after reconnecting or restarting
   a client. A persisted `running_*`, `needs_input`, or `blocked` stage can be
   retried with `continuation_prompt`.

The state retains every execution under `attempts`; retries do not erase prior
results. Approval records include the caller-declared actor, note, and timestamp.
These records are audit context, not proof of identity.

Each advance runs exactly one agent. State is stored under
`.deer-flow/product-engineering-pipelines/` and partitioned by a one-way hash of
the supplied `user_id`. The standalone MCP server trusts this caller-supplied
identity and the approval booleans; it does not authenticate either value.
Multi-user deployments must place it behind an identity-aware proxy that binds
both to trusted application state. Direct exposure is single-tenant only.

## Interactive PRD Questions

`product-agent` writes unresolved product questions to `open-questions.md` and,
when user input is needed, returns an `INTERACTIVE_CLARIFICATION_QUEUE` block.

```yaml
INTERACTIVE_CLARIFICATION_QUEUE:
  module: <module>
  spec_id: <spec-id>
  status: needs_input
  questions:
    - id: Q1
      priority: high
      blocks_next_stage: true
      clarification_type: missing_info
      question: "Which user roles should see this field?"
      context: "This affects PRD scope, permissions, and tech design."
      options: []
```

The Product Agent does not directly show the interactive card. The Lead Agent
reads the queue, calls `ask_clarification`, records the answer, and asks
`product-agent` to update:

- `product-specs/<module>/<spec-id>/prd.md`
- `product-specs/<module>/<spec-id>/open-questions.md`
- `product-specs/<module>/<spec-id>/clarification-log.md`

## Suggested Prompt

```text
Use product-engineering-pipeline.
module: <module>
spec_id: <spec-id>

Requirement / Q&A:
...

Start with product-agent. After it finishes, remind me what is needed before
calling tech-agent.
```

## Gates

- `product-agent -> tech-agent`: PRD approval required.
- `tech-agent -> dev-agent`: technical design approval required.
- `dev-agent -> human-review`: code review required.

No agent should merge, release, push, touch production config, read secrets, or
operate on production data.

## Feishu Project Context

When a requirement contains a Feishu Project work-item id or link, all three
agents may use the configured `FeishuProjectMcp` connection to read the latest
work-item detail, comments, relations, project metadata, and user metadata. Their
tool allowlists intentionally omit Feishu Project mutation tools such as field,
state, comment, and work-item updates. The local connection uses the remote
Streamable HTTP endpoint directly; its deployment-wide user token is referenced
from `.env`, not stored inline in `extensions_config.json`. This connection does
not provide per-DeerFlow-user Feishu identity isolation.

## Write Boundaries

- `product-agent` may only write `product-specs/<module>/<spec-id>/`.
- `tech-agent` may only write `tech-specs/<module>/<spec-id>/` and must cite
  relative code paths for technical findings.
- `dev-agent` must read `risk-checklist.md` before editing. External MCP calls
  default to `dry_run=true`; when `dry_run=false`, the MCP caller must provide
  `allowed_paths` and the agent may only modify those paths.
