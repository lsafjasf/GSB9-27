"""持久化单调计数器 + 固定窗口聚合（修复版，仅标准库）。

修复点（对照 buggy_metrics.py）：
1. 计数与窗口明细一起持久化，重启后从落盘状态续接，窗口聚合守恒。
2. 落盘原子化：写临时文件 -> fsync -> os.replace -> fsync 目录，
   强杀/掉电只会留下"上一个完整版本"或"新版本"，不会出现半个文件。
3. 状态带 CRC32 校验和与格式版本号；损坏（截断/篡改）可被识别，
   默认策略是隔离损坏文件并抛 CorruptStateError，绝不静默从零开始；
   调用方必须显式传 on_corrupt="reset" 才会重置。
4. 状态带 updated_at 时间戳，超过 state_ttl 视为过期：归档旧文件后
   显式重置（recovered_from 可观测），同样不是静默行为。
5. 单调性：total 只增不减，重启后从持久化值继续累加，不会回退。
"""

import json
import os
import time
import zlib


class CorruptStateError(Exception):
    """落盘状态文件损坏（无法解析 / 校验和不匹配 / 版本不符）。"""


def _window_start(ts, window_seconds):
    return int(ts) - (int(ts) % window_seconds)


class MetricsStore:
    FORMAT_VERSION = 1

    def __init__(self, path, window_seconds=3600, state_ttl=7 * 24 * 3600,
                 clock=time.time, on_corrupt="raise"):
        """
        path:           状态文件路径
        window_seconds: 聚合窗口大小（默认 1 小时）
        state_ttl:      状态有效期（秒），超过则归档并显式重置
        clock:          可注入时钟，便于测试
        on_corrupt:     "raise"（默认，隔离后抛错）或 "reset"（显式重置）
        """
        if on_corrupt not in ("raise", "reset"):
            raise ValueError("on_corrupt 必须是 'raise' 或 'reset'")
        self.path = path
        self.window_seconds = window_seconds
        self.state_ttl = state_ttl
        self.clock = clock
        self.on_corrupt = on_corrupt
        # fresh | state | expired-reset | corrupt-reset
        self.recovered_from = "fresh"
        self._total = 0
        self._windows = {}  # window_start -> 该窗口内的增量
        self._updated_at = None
        self._load()

    # ---------- 编解码 ----------

    def _encode(self, state):
        payload = json.dumps(state, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        envelope = {
            "version": self.FORMAT_VERSION,
            "crc32": zlib.crc32(payload),
            "payload": state,
        }
        return json.dumps(envelope, sort_keys=True).encode("utf-8")

    def _decode(self, raw):
        try:
            envelope = json.loads(raw)
            if not isinstance(envelope, dict):
                raise ValueError("envelope 不是对象")
            if envelope["version"] != self.FORMAT_VERSION:
                raise ValueError("格式版本不符: %r" % (envelope["version"],))
            payload = json.dumps(envelope["payload"], sort_keys=True,
                                 separators=(",", ":")).encode("utf-8")
            if zlib.crc32(payload) != envelope["crc32"]:
                raise ValueError("CRC32 校验和不匹配")
            state = envelope["payload"]
            total = int(state["total"])
            if total < 0:
                raise ValueError("total 为负")
            windows = {int(k): int(v) for k, v in state["windows"].items()}
            if any(v < 0 for v in windows.values()):
                raise ValueError("窗口计数为负")
            if sum(windows.values()) != total:
                raise ValueError("窗口之和与 total 不守恒")
            updated_at = float(state["updated_at"])
        except (KeyError, TypeError, ValueError) as e:
            raise CorruptStateError("状态文件损坏: %s" % (e,)) from e
        return {"total": total, "windows": windows, "updated_at": updated_at}

    # ---------- 加载 ----------

    def _load(self):
        try:
            with open(self.path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return  # 首次启动，全新状态
        try:
            state = self._decode(raw)
        except CorruptStateError:
            quarantine = "%s.corrupt.%d" % (self.path, int(self.clock()))
            os.replace(self.path, quarantine)  # 隔离现场，便于排查
            if self.on_corrupt == "reset":
                self.recovered_from = "corrupt-reset"
                return
            raise CorruptStateError(
                "%s 已损坏，已隔离到 %s；"
                "如确认放弃历史计数，请显式使用 on_corrupt='reset'"
                % (self.path, quarantine))
        if self.clock() - state["updated_at"] > self.state_ttl:
            archive = "%s.expired.%d" % (self.path, int(self.clock()))
            os.replace(self.path, archive)  # 归档过期状态
            self.recovered_from = "expired-reset"
            return
        self._total = state["total"]
        self._windows = state["windows"]
        self._updated_at = state["updated_at"]
        self.recovered_from = "state"

    # ---------- 写入 ----------

    def incr(self, n=1, ts=None):
        if n < 0:
            raise ValueError("单调计数器不允许负增量")
        ts = self.clock() if ts is None else ts
        w = _window_start(ts, self.window_seconds)
        self._total += n
        self._windows[w] = self._windows.get(w, 0) + n
        self._updated_at = ts

    def flush(self):
        """原子落盘：tmp -> fsync -> replace -> fsync 目录。"""
        now = self.clock()
        windows = {str(k): v for k, v in sorted(self._windows.items())
                   if now - k <= self.state_ttl}  # 顺带裁剪过期窗口
        state = {
            "total": self._total,
            "windows": windows,
            "updated_at": self._updated_at if self._updated_at is not None else now,
        }
        blob = self._encode(state)
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)  # 同文件系统内原子替换
        dir_fd = os.open(os.path.dirname(os.path.abspath(self.path)),
                         os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def close(self):
        self.flush()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ---------- 读取 ----------

    @property
    def total(self):
        return self._total

    def window_aggregate(self, window_start):
        """某个固定窗口内的增量（重启前后之和）。"""
        return self._windows.get(int(window_start), 0)

    def window_curve(self):
        """所有窗口的聚合曲线 {window_start: count}，按窗口起始排序。"""
        return dict(sorted(self._windows.items()))
