# 保留策略清理（修复版）

## 问题

旧实现（`legacy_cleanup.py`）用 `atime >= mtime` 推断"已归档"：归档任务读取数据会刷新
atime，导致仍被引用、仍在归档中的数据被误判为可删。时间戳推断是根因。

## 修复

`cleanup.py` 只依据显式状态判定，一个 blob 可删当且仅当同时满足：

1. 不在 `refs.json`（被引用的数据不删）；
2. 不在 `archive_state.json` 的 `in_progress`（归档进行中的不删）；
3. 在 `archive_state.json` 的 `completed`（归档已完成的才可删）；
4. `now - mtime >= retention`（刚写入的不删，atime 永不参与判定）。

安全兜底：
- `refs.json` 缺失/损坏 → 拒绝删除，退出码 2；
- 审计日志 `cleanup_audit.jsonl` 写不了（如磁盘满）→ 中止，一条都不删；
- 删除前重新 stat，mtime 与扫描时不一致（并发写入）→ 跳过并记录。

## 运行

```sh
# 干跑：输出候选清单与每条判定依据，不删任何东西
python3 cleanup.py DATA_DIR --retention-seconds 3600

# 确认后执行：删除并写审计日志（每条被删数据的判据可回溯）
python3 cleanup.py DATA_DIR --retention-seconds 3600 --execute

# 回归测试（14 个用例，含 bug 复现）
python3 -m unittest test_cleanup -v
```

样例输出见 `examples/dry_run_sample.json` 与 `examples/audit_sample.jsonl`。

## 数据目录约定

```
DATA_DIR/
  <blob files>          # 数据本体，文件名即 blob id
  refs.json             # ["blob_id", ...] 仍被引用
  archive_state.json    # {"in_progress": [...], "completed": [...]}
  cleanup_audit.jsonl   # 清理自动生成，每行一条审计记录
```
