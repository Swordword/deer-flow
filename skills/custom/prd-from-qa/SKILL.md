---
name: prd-from-qa
description: 根据产品、业务、研发或用户的问答记录生成 Git 化 PRD。当用户要求把访谈记录、澄清问答、会议纪要、聊天上下文、需求收集记录整理成 PRD、验收标准、非目标范围或待确认问题时使用；当 Product Agent 需要从 llm-wiki 和问答材料生成 product-specs 下的需求文档时使用。
---

# PRD From QA

使用本 Skill 将“问答记录”整理为可评审、可追溯、可进入技术方案阶段的 PRD。PRD 是一次性需求产物，应写入 `product-specs/<module>/<spec-id>/`，不要写入 `llm-wiki/`。

## 与 llm-wiki 的关系

- `llm-wiki/` 保存长期稳定的业务知识：术语、规则、状态、页面/API/代码地图、Owner。
- `product-specs/` 保存当前这一次需求：背景、目标、用户故事、验收标准、非目标范围、待确认问题。
- 生成 PRD 前，应读取最小相关范围的 `llm-wiki`，但不要把旧需求或历史 PRD 当成当前需求事实。
- 如果问答暴露出新的长期规则，先在 PRD 的“后续沉淀”里记录，不要直接更新 `llm-wiki/`，除非用户明确要求。

## 输入

尽量收集这些信息；缺失时也可以先生成草案，并把缺口写入 `open-questions.md`。

- `module`：模块名或业务域。
- `spec_id`：需求编号；没有时用短横线命名，例如 `2026-07-add-status-filter`。
- `qa_source`：问答记录、会议纪要、聊天上下文或访谈材料。
- `requester`：需求提出人或业务 Owner。
- `target_users`：受影响用户。
- `related_wiki_paths`：可选，相关 llm-wiki 文件。
- `related_links`：可选，飞书文档、issue、设计稿、历史 PR/MR。

## 输出位置

默认写入：

```text
product-specs/<module>/<spec-id>/
  prd.md
  qa-source.md
  open-questions.md
  clarification-log.md
```

如用户只要求“整理一版 PRD 内容”而没有要求落盘，可以直接在回复中输出 PRD 草案；但进入 DeerFlow 产研流程时，应优先落盘。

## 必读参考

在生成或更新文件前读取：

- `references/qa-extraction.md`
- `templates/prd.md`
- `templates/open-questions.md`
- `templates/clarification-log.md`

如果任务涉及读取长期业务上下文，同时使用 `llm-wiki` Skill 的读取流程。

## 工作流

1. **确定范围**
   - 确认模块、spec_id、输出目录。
   - 判断这是新需求、需求变更、Bug 修复，还是调研型需求。
   - 找到相关 `llm-wiki` 文件；只读取当前需求需要的最小上下文。

2. **整理来源**
   - 将原始问答保存到 `qa-source.md`。
   - 按“已确认事实 / 推断 / 待确认 / 冲突信息”分类。
   - 不要把推断写成已确认事实。

3. **生成 PRD**
   - 使用 `templates/prd.md` 的结构。
   - 每个需求点都要尽量落到用户场景、产品行为、验收标准。
   - 不要写技术实现方案；技术细节只作为“实现提示”或“约束”简要链接。

4. **生成待确认问题**
   - 对缺失、矛盾、影响范围不明、风险较高的信息，写入 `open-questions.md`。
   - 问题要可回答，避免“请补充更多信息”这类空泛问题。
   - 将需要用户回答的问题整理为 `INTERACTIVE_CLARIFICATION_QUEUE`，供 Lead Agent 逐个调用 `ask_clarification`。
   - 阻塞技术方案的问题必须进入交互队列；非阻塞问题可以只保留在 `open-questions.md`。

5. **交互式确认**
   - Product Agent / subagent 不能直接依赖自己调用用户交互工具；它应输出 `INTERACTIVE_CLARIFICATION_QUEUE`。
   - Lead Agent 看到队列后，必须按优先级逐个调用 `ask_clarification`，而不是要求用户一次性手写所有答案。
   - 用户回答后，将答案追加到 `clarification-log.md`。
   - 根据答案更新 `prd.md` 与 `open-questions.md`：已解决问题标记为 `已确认`，仍未解决的保留为 `未确认` 或 `阻塞`。
   - 每轮最多提出 1 个高优先级问题，或最多 3 个强相关的低风险问题；避免一次性打断用户太久。

6. **自检**
   - PRD 是否能让 Tech Agent 写技术方案。
   - 验收标准是否可测试。
   - 非目标范围是否明确。
   - 权限、隐私、资金、删除、生产配置等风险是否标出。
   - 所有不确定内容是否进入 `open-questions.md`。
   - 所有阻塞型待确认问题是否已进入 `INTERACTIVE_CLARIFICATION_QUEUE`。

## 交互式问题协议

当存在需要用户确认的问题时，最终回复必须包含：

```yaml
INTERACTIVE_CLARIFICATION_QUEUE:
  module: <module>
  spec_id: <spec-id>
  status: needs_input | clear
  questions:
    - id: Q1
      priority: high | medium | low
      blocks_next_stage: true | false
      clarification_type: missing_info | ambiguous_requirement | approach_choice | risk_confirmation | suggestion
      question: <给用户看的具体问题>
      context: <为什么需要确认>
      options:
        - <可选项 1>
        - <可选项 2>
      update_targets:
        - product-specs/<module>/<spec-id>/prd.md
        - product-specs/<module>/<spec-id>/open-questions.md
```

Lead Agent 应按队列调用：

```text
ask_clarification(
  question=<question>,
  clarification_type=<clarification_type>,
  context=<context>,
  options=<options or null>
)
```

如果没有需要交互的问题，输出：

```yaml
INTERACTIVE_CLARIFICATION_QUEUE:
  module: <module>
  spec_id: <spec-id>
  status: clear
  questions: []
```

## 写作规则

- 使用中文，除非用户或项目文档明确使用英文。
- 用产品语言描述“用户看到什么、能做什么、系统应该如何响应”。
- 避免把代码路径、类名、SQL 字段塞进正文；必要时放在“参考来源”或“实现提示”。
- 对问答中没有直接确认的信息，用“待确认”或“推断”标注。
- 验收标准使用可验证表达，例如“当…时，应…”，不要写“体验良好”“逻辑正确”。
- 不在 PRD 中承诺发布时间、负责人排期或上线策略，除非来源材料明确确认。

## 禁止事项

- 不要修改业务代码。
- 不要修改数据库 migration。
- 不要把一次性需求内容写入 `llm-wiki/`。
- 不要编造业务规则、权限范围、埋点口径或数据口径。
- 不要把密钥、生产数据、用户隐私明文写入 PRD。
