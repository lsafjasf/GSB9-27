"""磁盘上界缓冲（FIFO JSONL + 持久化确认游标）。

存储布局（目录下两个文件）：
  buffer.jsonl  追加写入的记录，每行一条 JSON；
  state.json    {"start": S, "dropped": D}
      start   —— 已被消费（已确认上报 或 被丢弃）的行数；
                 第 S 行起才是未上报的积压数据；
      dropped —— 累计丢弃条数（断网期间被上界策略丢弃的计数）。

丢弃策略：DROP_OLDEST（丢弃最旧的未上报记录，保留最新数据）。
游标只在“服务端已确认接受”后推进，因此进程崩溃重启后未确认数据仍会重发，
而已确认的数据绝不会被再次上报。
"""

import json
import os
from typing import Any, Dict, Iterable, List, Optional

DROP_OLDEST = "drop_oldest"


class DiskBuffer:
    def __init__(
        self,
        directory: str,
        max_records: int = 10_000,
        max_bytes: Optional[int] = None,
        compact_after: int = 1024,
    ) -> None:
        if max_records <= 0:
            raise ValueError("max_records 必须为正整数")
        self._dir = directory
        self._data_path = os.path.join(directory, "buffer.jsonl")
        self._state_path = os.path.join(directory, "state.json")
        self._tmp_state_path = self._state_path + ".tmp"
        self._max_records = int(max_records)
        self._max_bytes = max_bytes
        self._compact_after = max(1, int(compact_after))

        os.makedirs(directory, exist_ok=True)
        self._start, self._dropped = self._load_state()
        self._total = self._count_lines()
        # 数据文件缺失（被清理）时游标归零，避免游标悬空。
        if self._start > self._total:
            self._start, self._dropped = 0, 0
            self._persist_state()

    # ---- 公开属性 -----------------------------------------------------------
    @property
    def pending(self) -> int:
        """当前积压（尚未确认上报）的记录数。"""
        return self._total - self._start

    @property
    def dropped(self) -> int:
        """生命周期内累计丢弃的记录数（持久化，跨重启累计）。"""
        return self._dropped

    @property
    def max_records(self) -> int:
        return self._max_records

    # ---- 写入 ---------------------------------------------------------------
    def append(self, record: Dict[str, Any]) -> None:
        """写入一条记录；缓冲满时按 DROP_OLDEST 丢弃最旧记录。"""
        if self.pending >= self._max_records:
            self._drop_oldest(self.pending - self._max_records + 1)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with open(self._data_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._total += 1
        if self._max_bytes is not None and os.path.getsize(self._data_path) > self._max_bytes:
            # 字节数上界：丢弃最旧记录并立即压缩（否则游标前移不缩小文件）。
            while self.pending > 0 and os.path.getsize(self._data_path) > self._max_bytes:
                self._drop_oldest(max(1, self.pending // 2))
                self._compact()
        if self._start >= self._compact_after:
            self._compact()

    # ---- 读取与确认 ---------------------------------------------------------
    def read_batch(self, max_n: int) -> List[Dict[str, Any]]:
        """读取从确认游标起的至多 max_n 条记录（不移动游标）。"""
        if max_n <= 0 or self.pending == 0:
            return []
        n = min(max_n, self.pending)
        out: List[Dict[str, Any]] = []
        with open(self._data_path, "r", encoding="utf-8") as fh:
            for idx, line in enumerate(fh):
                if idx < self._start:
                    continue
                out.append(json.loads(line))
                if len(out) >= n:
                    break
        return out

    def ack(self, n: int) -> None:
        """确认前 n 条已被服务端接受，推进游标并按需压缩文件。"""
        if n <= 0:
            return
        if n > self.pending:
            raise ValueError(f"确认条数 {n} 超过积压数 {self.pending}")
        self._start += n
        self._persist_state()
        if self._start >= self._compact_after or self.pending == 0:
            self._compact()

    def close(self) -> None:
        if self.pending == 0 and os.path.exists(self._data_path):
            self._compact()

    # ---- 内部实现 -----------------------------------------------------------
    def _drop_oldest(self, k: int) -> None:
        k = min(k, self.pending)
        if k <= 0:
            return
        self._start += k
        self._dropped += k
        self._persist_state()

    def _compact(self) -> None:
        """把已消费（游标之前）的行物理删除，重写数据文件。"""
        if self._start == 0:
            return
        kept = self.read_all_pending()
        tmp_path = self._data_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            for rec in kept:
                fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, self._data_path)
        self._total = len(kept)
        self._start = 0
        self._persist_state()

    def read_all_pending(self) -> Iterable[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if self.pending == 0:
            return out
        with open(self._data_path, "r", encoding="utf-8") as fh:
            for idx, line in enumerate(fh):
                if idx < self._start:
                    continue
                out.append(json.loads(line))
        return out

    def _count_lines(self) -> int:
        if not os.path.exists(self._data_path):
            return 0
        total = 0
        with open(self._data_path, "rb") as fh:
            for _ in fh:
                total += 1
        return total

    def _load_state(self):
        try:
            with open(self._state_path, "r", encoding="utf-8") as fh:
                st = json.load(fh)
            return int(st.get("start", 0)), int(st.get("dropped", 0))
        except (FileNotFoundError, ValueError, KeyError):
            return 0, 0

    def _persist_state(self) -> None:
        payload = json.dumps({"start": self._start, "dropped": self._dropped})
        with open(self._tmp_state_path, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(self._tmp_state_path, self._state_path)
