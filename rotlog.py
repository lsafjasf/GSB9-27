"""rotlog: 按大小/时间触发的日志轮转库（仅标准库）。

特性：
- 大小(max_bytes) 与 时间(interval 秒) 两种触发条件，可同时启用。
- 轮转瞬间不丢行、不拆行：先轮转再写入，一行永远完整落在一个文件里。
- 旧文件可 gzip 压缩；按保留数量(max_backups) 与 总容量(max_total_bytes) 清理，
  绝不删除正在写入的活动文件，清理返回统计。
- 并发安全：write/rotate/close 共用一把锁，轮转采用
  “关闭 -> 改名 -> 重开新文件” 的切换方式，锁内完成，
  写入方永远拿不到已关闭的句柄。
- 轮转失败（如磁盘只读）时降级为继续写当前文件并记录错误，
  带退避重试，不丢行。
- 时间回拨安全：回拨期间不触发时间轮转，恢复后按原节奏轮转。
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field


@dataclass
class CleanupStats:
    """一次清理的统计结果。"""

    deleted_files: int = 0
    freed_bytes: int = 0
    remaining_files: int = 0
    remaining_bytes: int = 0

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return (
            f"CleanupStats(deleted={self.deleted_files}, "
            f"freed={self.freed_bytes}B, remaining={self.remaining_files} files "
            f"/ {self.remaining_bytes}B)"
        )


@dataclass
class WriterStats:
    """写入器累计统计。"""

    lines_written: int = 0
    bytes_written: int = 0
    rotations: int = 0
    rotate_errors: int = 0
    time_rollbacks: int = 0
    last_error: str | None = None
    last_cleanup: CleanupStats = field(default_factory=CleanupStats)


class RotatingLogWriter:
    """线程安全的轮转日志写入器。

    参数：
        path:             活动日志文件路径。
        max_bytes:        大小触发阈值；None 表示不启用。
        interval:         时间触发间隔（秒）；None 表示不启用。
        max_backups:      最多保留的旧文件个数；None 表示不限。
        max_total_bytes:  旧文件总容量上限（字节）；None 表示不限。
        compress:         轮转后是否 gzip 压缩旧文件。
        sync:             每次 write 后是否 flush（保证落盘，牺牲吞吐）。
        time_fn:          时钟函数（测试可注入假时钟）。
        retry_interval:   轮转失败后的重试退避秒数。
    """

    def __init__(
        self,
        path: str,
        *,
        max_bytes: int | None = None,
        interval: float | None = None,
        max_backups: int | None = None,
        max_total_bytes: int | None = None,
        compress: bool = True,
        sync: bool = False,
        time_fn=time.time,
        retry_interval: float = 1.0,
    ) -> None:
        if max_bytes is None and interval is None:
            raise ValueError("max_bytes 与 interval 至少启用一个")
        self.path = os.path.abspath(path)
        self.max_bytes = max_bytes
        self.interval = interval
        self.max_backups = max_backups
        self.max_total_bytes = max_total_bytes
        self.compress = compress
        self.sync = sync
        self._time_fn = time_fn
        self.retry_interval = retry_interval

        self._lock = threading.Lock()
        self._seq = 0
        self._closed = False
        self._size = 0
        self._backoff_until = 0.0
        self._last_seen_wall = float("-inf")
        self._next_rotate_time = (
            self._time_fn() + interval if interval is not None else float("inf")
        )
        self.stats = WriterStats()

        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._file = open(self.path, "ab")
        self._size = os.path.getsize(self.path)
        base = re.escape(os.path.basename(self.path))
        self._backup_re = re.compile(base + r"\.\d{8}-\d{6}\.\d{6}(\.gz)?$")

    # ------------------------------------------------------------------ API

    def write(self, data: str | bytes) -> None:
        """写入一条记录（建议以 \n 结尾；一行不会被拆到两个文件）。"""
        if isinstance(data, str):
            data = data.encode("utf-8")
        with self._lock:
            if self._closed:
                raise ValueError("writer 已关闭")
            now = self._time_fn()
            if now < self._last_seen_wall:
                self.stats.time_rollbacks += 1
            self._last_seen_wall = max(self._last_seen_wall, now)
            if self._should_rotate(len(data), now):
                self._rotate_locked(now)
            self._file.write(data)
            self._size += len(data)
            self.stats.lines_written += 1
            self.stats.bytes_written += len(data)
            if self.sync:
                self._file.flush()

    def write_line(self, line: str) -> None:
        """写入一行，自动补换行。"""
        if not line.endswith("\n"):
            line += "\n"
        self.write(line)

    def flush(self) -> None:
        with self._lock:
            if not self._closed:
                self._file.flush()

    def rotate(self) -> None:
        """手动触发一次轮转（测试/运维用）。"""
        with self._lock:
            if self._closed:
                raise ValueError("writer 已关闭")
            self._rotate_locked(self._time_fn(), force=True)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._file.flush()
                self._file.close()
                self._closed = True

    def __enter__(self) -> "RotatingLogWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------- 内部逻辑

    def _should_rotate(self, incoming: int, now: float) -> bool:
        if now < self._backoff_until:  # 轮转失败退避中
            return False
        if self.max_bytes is not None and self._size > 0:
            # 先轮转再写：当前行完整进入新文件，绝不拆行；
            # self._size > 0 保证单行超过阈值时不会空转。
            if self._size + incoming > self.max_bytes:
                return True
        if now >= self._next_rotate_time:
            return True
        return False

    def _rotate_locked(self, now: float, force: bool = False) -> None:
        """锁内执行：关闭 -> 改名(压缩) -> 重开。失败则重开旧文件继续写。"""
        try:
            self._file.close()
            self._seq += 1
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
            dest = f"{self.path}.{stamp}.{self._seq:06d}"
            if self.compress:
                gz = dest + ".gz"
                with open(self.path, "rb") as src, gzip.open(gz, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.unlink(self.path)
            else:
                os.rename(self.path, dest)
            self._file = open(self.path, "ab")
            self._size = 0
            self.stats.rotations += 1
            if self.interval is not None:
                # 基于当前时间推进，时间回拨时 now 偏小，自然推迟下次轮转，
                # 不会产生补偿性连续轮转。
                self._next_rotate_time = now + self.interval
            self._backoff_until = 0.0
            self.stats.last_cleanup = self._cleanup_locked()
        except OSError as exc:
            # 磁盘只读/满等：重开活动文件（append），继续写，不丢行。
            self.stats.rotate_errors += 1
            self.stats.last_error = f"{type(exc).__name__}: {exc}"
            self._file = open(self.path, "ab")
            self._size = os.path.getsize(self.path)
            self._backoff_until = self._time_fn() + self.retry_interval
            if force:
                raise

    def _backups_locked(self) -> list[tuple[str, int]]:
        """列出全部旧文件 (路径, 大小)，按名字（=时间+序号）升序。"""
        directory = os.path.dirname(self.path)
        out = []
        for name in os.listdir(directory):
            if self._backup_re.match(name):
                p = os.path.join(directory, name)
                try:
                    out.append((p, os.path.getsize(p)))
                except OSError:
                    continue
        out.sort(key=lambda t: t[0])
        return out

    def _cleanup_locked(self) -> CleanupStats:
        """按数量与总容量清理旧文件（最旧的先删），绝不碰活动文件。"""
        stats = CleanupStats()
        try:
            backups = self._backups_locked()
        except OSError:
            return stats

        def drop_oldest() -> None:
            p, size = backups.pop(0)
            try:
                os.unlink(p)
            except OSError:
                return
            stats.deleted_files += 1
            stats.freed_bytes += size

        if self.max_backups is not None:
            while len(backups) > self.max_backups:
                drop_oldest()
        if self.max_total_bytes is not None:
            while backups and sum(s for _, s in backups) > self.max_total_bytes:
                drop_oldest()
        stats.remaining_files = len(backups)
        stats.remaining_bytes = sum(s for _, s in backups)
        return stats
