# LLM Wiki 结构参考

创建或重组 LLM wiki、添加模块、定义检索/索引 metadata 时使用本参考。

## 目录约定

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
    adr-0001-example.md
  examples/
    <module>/
      historical-prd.md
      historical-mr.md
```

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `README.md` | Wiki 目的、模块列表、贡献规则、来源优先级 |
| `glossary.md` | 跨模块术语和标准命名 |
| `overview.md` | 模块目的、用户、关键流程、上下游依赖 |
| `business-rules.md` | 长期规则、约束、不变量、边界场景 |
| `states.md` | 状态机、枚举含义、允许的状态流转 |
| `pages.md` | 产品页面地图：页面、字段、用户动作 |
| `api-map.md` | API 路由、请求/响应含义、前端调用方 |
| `code-map.md` | 代码入口、常见修改路径、测试位置 |
| `ownership.md` | Owner、Reviewer、高风险区域、升级规则 |
| `faq.md` | 高频问题和已知陷阱 |
| `decisions/` | 带上下文和取舍的架构/产品决策 |
| `examples/` | 历史 PRD、技术方案、MR、事故的短链接/摘要 |

## 最小模块模板

```md
# <Module Name>

## Purpose

## Users and Scenarios

## Core Concepts

## Important Rules

## Related Files

## Sources
```

## 来源优先级

按以下顺序优先使用来源：

1. 当前代码和测试
2. 相关需求已确认的 PRD/技术方案
3. 历史 MR/commit 和 Review 评论
4. 现有产品或设计文档
5. 团队记忆或对话记录；确认前必须明确标记为未验证

## 向量索引 Metadata

使用 metadata 让 Agent 能按 scope 和 module 过滤：

```json
{
  "doc_scope": "llm_wiki",
  "module": "robot",
  "doc_type": "business_rule",
  "path": "llm-wiki/modules/robot/business-rules.md",
  "heading": "Robot status rules",
  "commit_sha": "abc123",
  "updated_at": "2026-07-09"
}
```

对于 PRD/spec chunk，包含 `spec_id`：

```json
{
  "doc_scope": "product_spec",
  "module": "robot",
  "spec_id": "2026-07-add-status-filter",
  "doc_type": "acceptance",
  "path": "product-specs/robot/2026-07-add-status-filter/acceptance.md"
}
```

## Index Manifest

manifest 文件放在 `indexes/`。不要提交真实向量 payload。

```json
{
  "collection": "xinfeng_llm_wiki",
  "last_indexed_commit": "abc123",
  "documents": [
    {
      "path": "llm-wiki/modules/robot/business-rules.md",
      "sha": "file-content-hash",
      "chunks": [
        {
          "chunk_id": "robot-business-rules-001",
          "heading": "Robot status rules",
          "hash": "chunk-content-hash",
          "qdrant_point_id": "point-id"
        }
      ]
    }
  ]
}
```

## 反模式

- 不要把一次性需求验收标准放进 `llm-wiki/`。
- 不要把整篇 PRD 复制到 wiki 文件里。
- 不要在没有 `doc_scope` 过滤的情况下让向量检索搜索所有目录。
- 不要把不确定的历史行为写成长期规则。
- 不要把密钥、凭证、私有 token 或生产数据写进 wiki。
