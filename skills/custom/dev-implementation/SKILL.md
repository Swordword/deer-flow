---
name: dev-implementation
description: 根据 llm-wiki、product-specs 和 tech-specs 执行受控开发实现。当用户要求按已确认 PRD 和技术方案改代码、补测试、跑检查、生成实现报告或 PR/MR 描述时使用；当 Dev Agent 需要消费 tech-spec-from-prd 产出的 tech-design、implementation-plan、test-plan、risk-checklist 并完成开发任务时使用。
---

# Dev Implementation

使用本 Skill 将已确认的 PRD 和技术方案落地为代码修改、测试补充、验证结果和 PR/MR 上下文。它是产研链路中的执行阶段：

```text
llm-wiki
  -> product-specs/<module>/<spec-id>/
  -> tech-specs/<module>/<spec-id>/
  -> 代码修改 + implementation-results/<module>/<spec-id>/
```

## 输入边界

开始实现前必须读取：

- `product-specs/<module>/<spec-id>/prd.md`
- `tech-specs/<module>/<spec-id>/tech-design.md`
- `tech-specs/<module>/<spec-id>/implementation-plan.md`
- `tech-specs/<module>/<spec-id>/test-plan.md`
- `tech-specs/<module>/<spec-id>/risk-checklist.md`

按需读取：

- `product-specs/<module>/<spec-id>/open-questions.md`
- 与当前实现有关的最小 `llm-wiki/` 文件
- 项目根 `AGENTS.md` 和相关模块 `AGENTS.md`
- 代码中的测试、接口、页面、数据模型、迁移说明

如果技术方案缺失、PRD 未确认、或 `risk-checklist.md` 中存在未审批的高风险项，不要继续实现；先向用户说明阻塞点。

## 输出位置

默认写入：

```text
implementation-results/<module>/<spec-id>/
  implementation-report.md
  test-result.md
  pr-description.md
```

也可以在用户要求时更新 `tech-specs/<module>/<spec-id>/` 中的实现偏差记录，但不要静默改写已确认 PRD 或技术方案。

## 必读参考

实现前读取：

- `references/implementation-workflow.md`
- `templates/implementation-report.md`
- `templates/test-result.md`
- `templates/pr-description.md`

## 工作流

1. **确认可开发状态**
   - 读取 PRD、技术方案、实现计划、测试计划、风险清单。
   - 确认没有阻塞型待确认问题。
   - 确认允许修改范围、禁止修改范围和高风险审批要求。

2. **建立任务清单**
   - 从 `implementation-plan.md` 拆出小步骤。
   - 每一步绑定目标文件范围和验证方式。
   - 不要在未完成核对前直接改代码。

3. **按最小范围修改代码**
   - 先读再写，保持修改局部。
   - 优先遵循现有代码风格和模块接口。
   - 不为一个需求引入大范围重构。
   - 任何偏离技术方案的实现选择，都写入实现报告。

4. **补测试**
   - 按 `test-plan.md` 覆盖 PRD 验收标准。
   - 能自动化的优先自动化；不能自动化的写入手工验证。
   - 不删除或跳过现有测试来制造通过。

5. **运行检查**
   - 按技术方案或项目文档中的命令运行 lint、单测、集成测试、构建。
   - 命令未知时先从项目文档和 package/pyproject/Makefile 中确认。
   - 失败时记录命令、失败摘要、原因判断和下一步建议。

6. **生成实现产物**
   - `implementation-report.md`：实现摘要、文件变更、方案偏差、风险处理。
   - `test-result.md`：执行命令、结果、失败原因、未覆盖项。
   - `pr-description.md`：面向 Reviewer 的 PR/MR 描述。

7. **收尾自检**
   - 改动是否覆盖 PRD 验收标准。
   - 改动是否遵守技术方案和允许范围。
   - 测试结果是否真实记录。
   - 高风险项是否已审批或未触碰。
   - PR 描述是否包含 PRD、技术方案、测试结果和风险说明。

## 偏差处理

实现过程中发现技术方案不准确时：

1. 停止扩大修改。
2. 写清楚实际代码证据。
3. 判断是否能在不扩大 PRD 范围的前提下小幅调整。
4. 小幅调整可继续，但必须记录在 `implementation-report.md`。
5. 影响接口、数据、权限、安全、范围或测试策略时，必须请求人工确认。

## Git 规则

- 默认可以修改工作区文件，但不要自动合并主分支。
- 只有用户明确要求时才创建 commit、branch、push 或 PR/MR。
- 创建 PR/MR 前，必须生成或更新 `pr-description.md`。
- PR/MR 描述必须标明 AI 参与，并链接 PRD、技术方案和测试结果。

## 禁止事项

- 不要直接发布生产。
- 不要直接合并主分支。
- 不要读取或写入密钥、生产数据、用户隐私明文。
- 不要修改生产配置、支付/资金、删除脚本、权限核心逻辑，除非风险清单明确允许且用户已确认。
- 不要绕过失败测试、删除测试或降低断言来让检查通过。
- 不要把实现细节写入 `llm-wiki/`，除非用户明确要求做长期知识沉淀。
