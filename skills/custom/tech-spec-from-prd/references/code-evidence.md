# Code Evidence Contract

M2 用它把一次代码调查变成 M3 可复用的导航结果，避免两个 Agent 重复扫描仓库。

## 读取策略

1. `repo_prepare(repository, ref)` 只调用一次。
2. 先读模块 `code-map.md`，再用本地 `rg`/`grep` 搜索符号。
3. 优先批量读取同一调用链上的少量文件；不要从仓库根目录逐层浏览。
4. 每条关键设计判断至少对应一条代码证据。
5. 路径使用仓库相对路径，不复制源码正文。

## `code-evidence.json`

```json
{
  "schema_version": 1,
  "repository": "popbee-frontend",
  "ref": "main",
  "commit_sha": "40-character-sha",
  "workspace_path": "/mnt/user-data/workspace/repos/popbee-frontend",
  "scope": ["src/modules/example", "tests/example"],
  "evidence": [
    {
      "path": "src/modules/example/service.ts",
      "symbol": "ExampleService.update",
      "lines": "42-88",
      "finding": "当前更新入口及事务边界",
      "supports": ["TECH-01", "AC-02"]
    }
  ],
  "missing_evidence": [],
  "generated_at": "RFC3339 timestamp"
}
```

`lines` 只用于帮助定位，代码变化后可能漂移；`path + symbol + commit_sha` 才是主要定位键。找不到证据时写入 `missing_evidence`，不要用猜测补齐。
