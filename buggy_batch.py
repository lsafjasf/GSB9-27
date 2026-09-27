"""有缺陷的批处理实现（仅用于复现 bug，请勿在生产使用）。

Bug 描述：
- 成功状态不落盘，重跑（新进程）会把已成功记录再处理一遍 -> 副作用重复。
- 失败记录被写入 skip 文件，重跑时直接跳过 -> 永远不再重试。
"""

import json
import os


class BuggyBatchProcessor:
    def __init__(self, record_ids, handler, store_path):
        self._record_ids = list(record_ids)
        self._handler = handler
        self._store_path = store_path

    def _load_skip(self):
        if os.path.exists(self._store_path):
            with open(self._store_path, "r", encoding="utf-8") as fh:
                return set(json.load(fh).get("skip", []))
        return set()

    def _save_skip(self, skip):
        with open(self._store_path, "w", encoding="utf-8") as fh:
            json.dump({"skip": sorted(skip)}, fh)

    def run(self):
        skip = self._load_skip()
        for rid in self._record_ids:
            if rid in skip:
                continue  # bug2: 上次失败的记录被永久跳过，不再重试
            try:
                self._handler(rid)
            except Exception:
                skip.add(rid)
                self._save_skip(skip)
            # bug1: 成功没有任何持久化记录，重跑时全部重新处理
        return {"skipped": len(skip)}
