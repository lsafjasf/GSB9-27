# 保留策略清理（修复版）

判定不再使用 mtime/atime（归档任务会改写它们），改用仓库自有信号：
`refs.json`（引用）、`archive/<id>.lock`（归档进行中，带 TTL）、
`meta/<id>.json`（写入时间，宽限期）。信息缺失一律 fail-safe 保留。

## 运行命令

```bash
# 回归测试（15 个用例）
python3 -m unittest test_cleanup -v

# 复现旧实现的误删（3 个场景）+ 验证修复
python3 reproduce_bug.py

# 干跑：输出候选清单与判定依据，写 journal/plan-*.jsonl，不删任何文件
python3 storage_cleanup.py dry-run --root /path/to/store --min-age 3600

# 执行：按计划删除，逐条重新校验（防并发写入），写 journal/audit-*.jsonl
python3 storage_cleanup.py execute --root /path/to/store --plan <plan文件>
```

## 文件

- `storage_cleanup.py` — 修复后的实现（DataStore + 清理策略 + CLI），仅标准库
- `reproduce_bug.py` — 误删复现：外部改时间 / 归档进行中 / 清理与写入并发
- `test_cleanup.py` — 回归测试（含空目录、全部可删、引用缺失、磁盘写满）
- `samples/` — 干跑输出、计划文件、执行输出、审计日志样例

## 判定码

| reason | 含义 |
| --- | --- |
| `KEEP_REFERENCED` | 仍被引用，保留 |
| `KEEP_ARCHIVE_ACTIVE` | 归档任务进行中，保留 |
| `KEEP_WITHIN_GRACE` | 刚写入（宽限期内），保留 |
| `KEEP_REFS_MISSING` | 引用清单缺失，fail-safe 保留 |
| `KEEP_META_MISSING` | 元数据缺失，fail-safe 保留 |
| `DELETE_ELIGIBLE` | 无引用、无活动归档、超过宽限期，可删 |
