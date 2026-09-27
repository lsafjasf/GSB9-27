"""测试用假数据源：支持延迟、故障注入、闸门（模拟在途响应）。"""

import threading
import time


class FakeSource:
    def __init__(self, latency=0.0):
        self._lock = threading.Lock()
        self._data = {}
        self._failures = []
        self.latency = latency
        self.gate = None  # 设置后，read 在取值后阻塞直到 gate 打开（模拟在途响应）
        self.entered = threading.Event()
        self.read_count = 0
        self.write_count = 0

    def fail_next(self, exc):
        with self._lock:
            self._failures.append(exc)

    def read(self, key):
        with self._lock:
            self.read_count += 1
            if self._failures:
                raise self._failures.pop(0)
            if key not in self._data:
                raise KeyError(key)
            value = self._data[key]  # 值在进入时确定，模拟"响应已在途"
        self.entered.set()
        if self.gate is not None:
            self.gate.wait()
        if self.latency:
            time.sleep(self.latency)
        return value

    def write(self, key, value):
        with self._lock:
            self.write_count += 1
            self._data[key] = value
