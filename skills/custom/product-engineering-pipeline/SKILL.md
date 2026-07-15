---
name: product-engineering-pipeline
description: 编排 M1 Product Agent、M2 Tech Agent、M3 Dev Agent 的产研流水线。当用户要求从需求或问答进入 PRD、技术方案、开发实现，或要求每个 agent 完成后提醒/交接给下一个 agent 时使用。
---

# Product Engineering Pipeline

使用本 Skill 由 Lead Agent 串联三段 subagent 工作流：

```text
M1 product-agent
  -> product-specs/<module>/<spec-id>/
  -> M2 tech-agent
  -> tech-specs/<module>/<spec-id>/
  -> M3 dev-agent
  -> implementation-results/<module>/<spec-id>/
  -> human-review
```

## Agent 职责

| Agent | 输入 | 输出 | Skill |
| --- | --- | --- | --- |
| `product-agent` | 原始需求/问答 + `llm-wiki` | `product-specs/<module>/<spec-id>/prd.md`、`open-questions.md` | `prd-from-qa` |
| `tech-agent` | 已确认 PRD + `llm-wiki` + 代码证据 | `tech-specs/<module>/<spec-id>/tech-design.md`、`implementation-plan.md`、`test-plan.md`、`risk-checklist.md` | `tech-spec-from-prd` |
| `dev-agent` | 已确认 PRD + 已确认技术方案 + `llm-wiki` | 代码改动、`implementation-report.md`、`test-result.md`、`pr-description.md` | `dev-implementation` |

## 代码快照交接

- M1 不读取代码。
- M2 在技术设计开始时调用一次 `repo_prepare`，把 `workspace_path` 与固定 `commit_sha` 写入 `code-evidence.json`。
- M3 调用 `repo_snapshot_status`，仅在 SHA 一致时复用同一线程快照；不要重新远程遍历仓库。
- GitLab MCP 只承担 Issue/MR/Commit/Diff 元数据和本地缺失代码兜底。
- `repo_snapshot` 未启用或仓库别名未配置时，M2 必须明确阻塞原因，不要退化为大量逐文件 MCP 调用。

## Lead Agent 编排规则

1. 启动阶段时，使用 `task` 工具选择对应 subagent：
   - PRD 阶段：`subagent_type="product-agent"`
   - 技术方案阶段：`subagent_type="tech-agent"`
   - 开发阶段：`subagent_type="dev-agent"`
2. 每个 subagent 结束后，读取它返回的 `NEXT_AGENT_REMINDER`。
3. 如果 `product-agent` 返回 `INTERACTIVE_CLARIFICATION_QUEUE.status=needs_input`：
   - Lead Agent 必须按优先级逐个调用 `ask_clarification`。
   - 每次只问 1 个高优先级问题，或最多 3 个强相关低风险问题。
   - 用户回答后，将答案交回 `product-agent`，要求它更新 `prd.md`、`open-questions.md` 和 `clarification-log.md`。
   - 队列清空或只剩非阻塞问题后，再提示用户进行 PRD approval。
4. 将 `NEXT_AGENT_REMINDER` 原样摘要给用户，并说明下一步需要什么确认。
5. 默认保留人工门禁：
   - `product-agent -> tech-agent`：需要 PRD approval。
   - `tech-agent -> dev-agent`：需要 tech design approval。
   - `dev-agent -> human-review`：需要人工 Code Review。
6. 只有当用户明确说“继续”“已确认”“自动进入下一步”，且 reminder `status=ready` 时，才调用下一个 agent。
7. 如果 reminder `status=needs_input`，先处理交互式待确认问题。
8. 如果 reminder `status=blocked`，不要调用下一个 agent；先列出阻塞问题。

## 外部 MCP 编排

当本 Skill 通过 `deerflow-agent-mcp` 暴露给外部客户端时，优先使用持久化工作流工具：

1. `start_product_engineering_pipeline` 创建工作流并只执行 M1；为支持首次调用断线恢复，客户端应预先生成并传入 32 位小写十六进制 `workflow_id`。
2. 用户确认 PRD 后，以同一个 `workflow_id` 调用 `advance_product_engineering_pipeline(prd_approved=true)`，只执行 M2。
3. 用户确认技术方案后，再调用 `advance_product_engineering_pipeline(tech_design_approved=true)`，只执行 M3。
4. 断线或客户端重启后，使用 `get_product_engineering_pipeline_status` 恢复状态。
5. `needs_input`、`blocked` 或遗留的 `running_*` 状态只能通过明确的 `continuation_prompt` 重试。
6. MCP server 本身不认证调用方传入的 `user_id` 和审批布尔值；多用户环境必须由可信认证代理绑定这些参数，不能直接暴露服务或共用默认的 `mcp` 身份。

工作流服务会解析 fenced yaml `AGENT_RESULT`，校验 agent、status、下一 Agent、审批 gate 与 required_inputs。契约不合法时状态必须停在 `blocked`。

## 标准 Handoff 块

每个 agent 的最终回复最后必须包含 fenced yaml `AGENT_RESULT` 块。`NEXT_AGENT_REMINDER` 放在 `AGENT_RESULT.next_agent_reminder` 内，旧消费方需要时也可以单独摘要展示：

```yaml
AGENT_RESULT:
  agent: <agent-name>
  status: ready | needs_input | blocked
  artifacts:
    - <path>
  blockers: []
  next_agent_reminder:
    next_agent: <agent-name>
    gate: <approval-or-review-gate>
    status: ready | needs_input | blocked
    required_inputs:
      - <path-or-input>
    suggested_prompt: |
      <prompt for the next agent>
```

## 交互式待确认问题

`product-agent` 可能返回：

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
      question: <question>
      context: <context>
      options: []
      update_targets:
        - product-specs/<module>/<spec-id>/prd.md
        - product-specs/<module>/<spec-id>/open-questions.md
```

Lead Agent 应调用：

```text
ask_clarification(
  question=<question>,
  clarification_type=<clarification_type>,
  context=<context>,
  options=<options or null>
)
```

收到用户答案后，Lead Agent 再调用 `product-agent`：

```text
Update <module>/<spec-id> PRD clarification artifacts with this answer:
- question_id: <Qx>
- answer: <user answer>

Update:
- product-specs/<module>/<spec-id>/prd.md
- product-specs/<module>/<spec-id>/open-questions.md
- product-specs/<module>/<spec-id>/clarification-log.md

Return the refreshed INTERACTIVE_CLARIFICATION_QUEUE and NEXT_AGENT_REMINDER.
```

## 推荐启动提示

```text
使用 product-engineering-pipeline。
模块：<module>
spec_id：<spec-id>
需求/问答如下：
...
先调用 product-agent 生成 PRD。完成后提醒我确认是否进入 tech-agent。
```

## 安全边界

- `product-agent` 只允许写 `product-specs/<module>/<spec-id>/`。
- `tech-agent` 只允许写 `tech-specs/<module>/<spec-id>/`，必须基于代码证据输出技术判断，不得修改源码。
- `tech-agent` 必须把仓库、固定 commit SHA、路径、符号和结论写入 `code-evidence.json`。
- `dev-agent` 必须先读取 `risk-checklist.md`；外部 MCP 调用中 `dry_run=true` 时不得改文件，`dry_run=false` 时只能改 `allowed_paths` 内的文件。
- 不要让 `tech-agent` 在 PRD 未确认时生成最终技术方案。
- 不要让 `dev-agent` 在技术方案未确认时改代码。
- 不要让任何 agent 自动合并、发布、触碰生产配置或读取密钥。
- 涉及权限、资金、隐私、删除、生产配置时，必须要求人工确认。
- 若需求来自飞书项目工作项，M1/M2/M3 可以通过 `FeishuProjectMcp` 读取详情、评论、关联项和元数据，但默认不得修改工作项字段、状态或评论。
