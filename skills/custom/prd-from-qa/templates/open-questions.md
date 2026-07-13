# 待确认问题

## 交互状态

- 当前状态：`needs_input | clear`
- 最近确认时间：
- 最近确认人：
- Lead Agent 交互轮次：

## 高优先级

| ID | 问题 | 为什么需要确认 | 阻塞下一阶段 | 建议确认人 | 状态 |
| --- | --- | --- | --- | --- | --- |
| Q1 |  |  | 是/否 |  | 未确认 |

## 中低优先级

| ID | 问题 | 为什么需要确认 | 阻塞下一阶段 | 建议确认人 | 状态 |
| --- | --- | --- | --- | --- | --- |
| Q2 |  |  | 是/否 |  | 未确认 |

## 冲突信息

| 冲突点 | 来源 A | 来源 B | 处理建议 |
| --- | --- | --- | --- |
|  |  |  |  |

## 长期知识候选

这些内容可能需要在需求完成后沉淀到 `llm-wiki/`：

- 

## INTERACTIVE_CLARIFICATION_QUEUE

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
      question: ""
      context: ""
      options: []
      update_targets:
        - product-specs/<module>/<spec-id>/prd.md
        - product-specs/<module>/<spec-id>/open-questions.md
```
