---
name: tech-spec-from-prd
description: 根据 llm-wiki 长期知识和 product-specs 中已确认的 PRD 生成 Git 化技术文档。当用户要求从 PRD、需求文档、验收标准生成技术方案、接口设计、影响范围、测试计划、风险清单或开发拆解时使用；当 Tech Agent 需要基于业务 wiki 和 PRD 产出 tech-specs 下的技术方案时使用。
---

# Tech Spec From PRD

使用本 Skill 将已确认 PRD 转换为可评审、可实现、可测试的技术文档。技术文档是一次性需求产物，应写入 `tech-specs/<module>/<spec-id>/`，不要写入 `llm-wiki/`。

## 与 llm-wiki / product-specs 的关系

- `llm-wiki/`：长期稳定上下文，包括业务规则、状态机、页面/API/代码地图、Owner、风险边界。
- `product-specs/<module>/<spec-id>/`：当前需求的产品事实来源，包括 PRD、验收标准、非目标范围、待确认问题。
- `tech-specs/<module>/<spec-id>/`：当前需求的技术方案、影响范围、测试计划、风险清单和开发拆解。

技术方案必须以 PRD 为输入边界。PRD 未确认或仍有阻塞型问题时，不要强行生成“可开发”方案；应输出草案并明确阻塞项。

## 输入

尽量收集这些信息：

- `module`：模块名或业务域。
- `spec_id`：需求编号，应与 `product-specs/<module>/<spec-id>/` 保持一致。
- `prd_path`：PRD 路径，通常是 `product-specs/<module>/<spec-id>/prd.md`。
- `open_questions_path`：待确认问题路径，可选。
- `related_wiki_paths`：相关 llm-wiki 文件。
- `repository_scope`：允许分析的前端、后端、数据库或测试目录。
- `repository` / `ref`：`repo_snapshot.repositories` 中的仓库别名，以及可选分支、标签或 commit。
- `risk_policy`：高风险文件、禁止修改目录、需要审批的目录。

## 输出位置

默认写入：

```text
tech-specs/<module>/<spec-id>/
  tech-design.md
  implementation-plan.md
  test-plan.md
  risk-checklist.md
  code-evidence.json
```

如用户只要求“先写一版方案内容”而没有要求落盘，可以直接在回复中输出草案；但进入 DeerFlow 产研流程时，应优先落盘。

## 必读参考

生成或更新技术文档前读取：

- `references/design-extraction.md`
- `templates/tech-design.md`
- `templates/implementation-plan.md`
- `templates/test-plan.md`
- `templates/risk-checklist.md`
- `references/code-evidence.md`

如果任务涉及长期业务上下文，同时使用 `llm-wiki` Skill 的读取流程。若 PRD 来自问答生成，先检查 `prd-from-qa` 输出的 `open-questions.md` 是否仍有阻塞型问题。

## 工作流

1. **确认输入状态**
   - 读取 PRD、待确认问题和相关 `llm-wiki`。
   - 判断 PRD 是否已足够进入技术设计。
   - 对仍未确认但不阻塞设计的问题，写入风险或假设；对阻塞问题，停止并说明。

2. **准备固定代码快照**
   - 若 `repo_prepare` 可用，按仓库别名调用一次并记录 `workspace_path`、`commit_sha`。
   - 同一需求的后续分析都复用该目录；不要对固定快照反复 `refresh`。
   - GitLab MCP 只查 Issue、MR、Commit、Diff 元数据，或在本地快照确实缺文件时兜底；禁止用它逐文件遍历源码。

3. **代码与数据证据收集**
   - 先读 `llm-wiki/modules/<module>/code-map.md`，再用本地 `rg`/`grep` 定位符号。
   - 根据 PRD 中的页面、接口、业务对象、状态、字段，定位前端入口、后端入口、数据模型、测试位置。
   - 建立 `页面/入口 -> API -> Service/Use Case -> Repository/DAO -> 表/模型` 的链路。
   - 只引用相关代码路径，不复制大段代码。
   - 按 `references/code-evidence.md` 写入 `code-evidence.json`，让 Dev Agent 直接复用路径和结论。

4. **生成技术方案**
   - 使用 `templates/tech-design.md`。
   - 写清影响范围、设计选择、接口/数据变化、兼容性、异常处理、安全边界。
   - 对每个关键设计点写明来源：PRD、llm-wiki、代码路径或待确认假设。

5. **生成实现计划**
   - 使用 `templates/implementation-plan.md`。
   - 将工作拆成可顺序执行的小步骤。
   - 每步包含目标、文件范围、验证方式。

6. **生成测试计划**
   - 使用 `templates/test-plan.md`。
   - 覆盖 PRD 验收标准、边界条件、回归范围和自动化测试建议。
   - 明确要跑的命令；未知时标记待确认，不要编造。

7. **生成风险清单**
   - 使用 `templates/risk-checklist.md`。
   - 标出权限、隐私、资金、删除、生产配置、数据迁移、兼容性、性能、外部依赖风险。
   - 明确哪些风险需要人工审批。

8. **自检**
   - 技术方案是否覆盖 PRD 的每条验收标准。
   - 修改范围是否在允许目录内。
   - 测试计划是否能验证核心行为。
   - 高风险操作是否被禁止或要求审批。
   - 不确定信息是否被放入假设、风险或待确认项。
   - `code-evidence.json` 是否固定到一个 commit SHA，且关键判断可回到本地路径和符号。

## 写作规则

- 使用中文，除非项目文档明确使用英文。
- 技术方案必须能让 Dev Agent 执行，但不要直接修改代码。
- 用相对路径引用代码、PRD、wiki 和测试文件。
- 不要把 PRD 原文整段复制到技术方案里；只引用必要需求点。
- 不要在没有代码证据时断言实现位置。
- 不要自行扩大需求范围；非目标范围要从 PRD 继承。
- 每个接口、数据、权限、状态变化都要说明兼容性和测试方式。

## 禁止事项

- 不要修改业务代码。
- 不要修改数据库 migration。
- 不要创建提交、分支或 PR。
- 不要把技术方案写入 `llm-wiki/`。
- 不要绕过 PRD 的待确认问题。
- 不要设计直接合并、直接发布、直接操作生产数据的流程。
