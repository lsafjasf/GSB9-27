"""有缺陷的指标实现（复现用例，请勿在生产使用）。

缺陷点：
1. 计数只活在进程内存里，落盘格式与读取格式不一致，
   重启后 _load() 永远解析失败并【静默从零开始】。
2. flush() 直接截断写目标文件，非原子、无校验和，
   强杀/掉电可能留下半个文件，且损坏无法被识别。
3. 聚合曲线直接取累计值快照，重启清零后曲线出现断层甚至倒退。
"""

import json
import time


class BuggyMetrics:
    def __init__(self, path, window_seconds=3600, clock=time.time):
        self.path = path
        self.window_seconds = window_seconds
        self.clock = clock
        self._total = 0
        self._snapshots = []  # [(ts, total)] 进程内快照，重启即丢
        self._load()

    def _load(self):
        try:
            with open(self.path, "r") as f:
                # BUG: flush() 写的是裸整数文本，这里却按 JSON 对象解析，
                # 永远抛异常，于是计数静默归零。
                self._total = json.load(f)["total"]
        except Exception:
            self._total = 0  # 静默从零开始，无任何告警

    def _window_start(self, ts):
        return int(ts) - (int(ts) % self.window_seconds)

    def incr(self, n=1, ts=None):
        ts = self.clock() if ts is None else ts
        self._total += n
        self._snapshots.append((ts, self._total))

    def flush(self):
        # BUG: 非原子写（先截断再写），无 fsync、无校验和。
        with open(self.path, "w") as f:
            f.write(str(self._total))

    def window_curve(self):
        """按固定窗口聚合：取每个窗口内最后一个累计快照。"""
        curve = {}
        for ts, total in self._snapshots:
            curve[self._window_start(ts)] = total
        return curve
