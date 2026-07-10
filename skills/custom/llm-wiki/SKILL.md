---
name: llm-wiki
description: 管理和使用 Git 化的 LLM wiki，用于沉淀长期有效的产品、业务、技术、代码地图和检索索引知识。当用户要求创建、读取、更新、校验、组织、索引、同步或基于 llm-wiki 推理时使用；当处理 Product Agent、Tech Agent、Dev Agent 上下文时使用；当判断信息应放入 llm-wiki 还是 product-specs/tech-specs 时使用；当把 llm-wiki 接入 Qdrant/向量检索、PRD Git 化流程、代码地图或 Agent Skills 时使用。
---

# LLM Wiki

使用本 Skill 维护长期项目知识，让知识对 Agent 清晰、可追溯、可复用。将 `llm-wiki/` 视为“系统如何运作”的长期上下文，而不是存放一次性需求细节的地方。

## 核心边界

按知识生命周期区分存放位置：`

| 内容 | 放到 |
| --- | --- |
| 稳定业务术语、模块规则、状态机、API 地图、代码地图、Owner、通用模式 | `llm-wiki/` |
| 某个具体需求、验收标准、原型、非目标范围、待确认问题 | `product-specs/<module>/<spec-id>/` |
| 某个具体需求的实现方案、API 契约、测试计划、风险清单 | `tech-specs/<module>/<spec-id>/` |
| Agent 操作规程、模板、工具规则 | DeerFlow `skills/` |
| 生成的索引元数据、chunk manifest、同步 checkpoint | `indexes/` |

如果一条信息只对某个需求成立，不要放进 `llm-wiki/`。如果一个已验收需求改变了长期规则，在需求完成后再更新 `llm-wiki/`。

## 读取流程

1. 先识别模块、任务阶段，以及当前是否有 `spec_id`。
2. 优先读取最小相关范围的 wiki 文件，通常是：
   - `llm-wiki/glossary.md`
   - `llm-wiki/modules/<module>/overview.md`
   - `llm-wiki/modules/<module>/business-rules.md`
   - `llm-wiki/modules/<module>/states.md`
   - `llm-wiki/modules/<module>/api-map.md`
   - `llm-wiki/modules/<module>/code-map.md`
3. Product Agent 场景只读取当前需求或明确相似的历史 `product-specs/`。
4. Tech Agent 场景读取已确认 PRD，再读取 wiki 中的代码/API 地图。
5. Dev Agent 场景读取已确认 PRD、已确认技术方案，以及保持业务正确性所需的最小 wiki 文件。
6. 如果使用向量检索，必须按 metadata 过滤（`doc_scope`、`module`、`spec_id`），不要直接检索整个仓库。

## 写入流程

修改 `llm-wiki/` 前，先判断新增的是哪类知识：

1. **新的长期领域规则**：更新所属模块文件，通常是 `business-rules.md` 或 `states.md`。
2. **新的页面/API/代码位置**：更新 `pages.md`、`api-map.md` 或 `code-map.md`。
3. **新的术语**：更新 `glossary.md`。
4. **带取舍的决策**：在 `llm-wiki/decisions/` 下新增或更新 ADR。
5. **历史案例**：在 `llm-wiki/examples/` 下添加短摘要，并链接 PRD、技术方案和 MR。

每次 wiki 更新都应尽量包含来源指针：代码路径、PRD 路径、技术方案路径、MR/commit 链接或文档链接。不要粘贴大段代码；用摘要加源文件链接。

## 推荐仓库结构

对于预期的 `xinfeng-agent-workflow` 类仓库，使用：

```text
llm-wiki/
  README.md
  glossary.md
  modules/
    <module>/
      overview.md
      business-rules.md
      states.md
      pages.md
      api-map.md
      code-map.md
      ownership.md
      faq.md
  decisions/
  examples/
product-specs/
tech-specs/
indexes/
scripts/
```

当创建新的 wiki 目录、添加模块、定义 Qdrant metadata 或设计校验/索引脚本时，读取 `references/wiki-structure.md`。

## 检索与索引规则

小规模 wiki 优先精确读取文件。只有当 wiki 和历史 specs 足够大、关键词/文件读取经常漏召回时，再接入 Qdrant/向量检索。

索引时保持 scope 清晰：

```text
doc_scope=llm_wiki      long-lived knowledge
doc_scope=product_spec  per-requirement product source
doc_scope=tech_spec     per-requirement implementation plan
```

使用独立 Qdrant collection，或使用严格 metadata 过滤。Product Agent 不能把旧 PRD 误当成当前长期规则。

## 校验清单

结束 wiki 相关任务前，检查：

- 信息是否按生命周期放在正确目录。
- 每条新增或修改的 wiki 事实是否有来源或明确 Owner。
- 更新是否聚焦模块知识，而不是复制 PRD。
- 同仓库内的 PRD、技术方案、代码或 MR 链接是否使用相对路径。
- index manifest 是否只保存元数据；真实向量数据应保存在 Qdrant 或其他向量库。
- 过期或不确定的信息是否标记为 `TODO` 或 `Needs verification`，而不是直接写成事实。
