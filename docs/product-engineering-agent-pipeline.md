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

## Write Boundaries

- `product-agent` may only write `product-specs/<module>/<spec-id>/`.
- `tech-agent` may only write `tech-specs/<module>/<spec-id>/` and must cite
  relative code paths for technical findings.
- `dev-agent` must read `risk-checklist.md` before editing. External MCP calls
  default to `dry_run=true`; when `dry_run=false`, the MCP caller must provide
  `allowed_paths` and the agent may only modify those paths.
