"""分段日志读取：支持日志轮转、断点续读、超大单行保护。"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

DEFAULT_MAX_LINE_BYTES = 64 * 1024 * 1024
SEGMENT_RE = re.compile(r"^segment-(\d+)\.log$")


class OversizedLineError(IOError):
    """单行超过 max_line_bytes 时抛出。"""


class SegmentReader:
    """按段顺序读取日志目录，段名形如 segment-000000.log，序号单调递增。"""

    def __init__(self, directory: str, max_line_bytes: int = DEFAULT_MAX_LINE_BYTES) -> None:
        self.directory = directory
        self.max_line_bytes = max_line_bytes
        self._fh = None
        self._segment: Optional[str] = None

    def list_segments(self) -> List[str]:
        try:
            names = os.listdir(self.directory)
        except FileNotFoundError:
            return []
        segs = [n for n in names if SEGMENT_RE.match(n)]
        segs.sort(key=lambda n: int(SEGMENT_RE.match(n).group(1)))
        return segs

    def open_at(self, segment: str, offset: int) -> None:
        self.close()
        self._fh = open(os.path.join(self.directory, segment), "rb")
        self._fh.seek(offset)
        self._segment = segment

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def _next_segment(self) -> Optional[str]:
        segs = self.list_segments()
        if not segs:
            return None
        if self._segment is None or self._segment not in segs:
            return segs[0]
        idx = segs.index(self._segment)
        return segs[idx + 1] if idx + 1 < len(segs) else None

    def readline(self) -> Optional[Tuple[bytes, str, int]]:
        """读取一行，返回 (line, segment, offset_after)。

        返回 None 表示当前没有更多完整行（EOF 或最后一行尚未写完）。
        读到段尾会自动切到下一段（日志轮转）。
        """
        while True:
            if self._fh is None:
                nxt = self._next_segment()
                if nxt is None:
                    return None
                self.open_at(nxt, 0)
                continue
            start = self._fh.tell()
            chunk = self._fh.readline(self.max_line_bytes + 1)
            if chunk == b"":
                nxt = self._next_segment()
                if nxt is None:
                    return None
                self.open_at(nxt, 0)
                continue
            if len(chunk) > self.max_line_bytes:
                raise OversizedLineError(
                    f"line in {self._segment} at offset {start} exceeds "
                    f"max_line_bytes={self.max_line_bytes}"
                )
            if not chunk.endswith(b"\n"):
                # 写入方尚未写完这一行：回退，等待下次读取
                self._fh.seek(start)
                return None
            return chunk, self._segment, self._fh.tell()
