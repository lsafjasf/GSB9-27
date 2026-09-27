"""未修复的读穿缓存实现（仅用于复现问题，请勿用于生产）。

读取路径：查缓存 -> 未命中回源 -> 写回缓存
写入路径：写数据源 -> 失效缓存

已知缺陷：
1. 回源写回无任何防护：慢回源拿到的旧值会在失效之后被写回缓存，
   导致后续读取长期读到旧值。
2. 回源异常被吞掉并以 None 写回缓存且永不过期，
   数据源恢复后调用方仍持续读到空值。
3. 无单飞：同一键的并发未命中会全部回源，放大数据源压力。
"""

import threading


class BuggyCache:
    def __init__(self, source):
        self.source = source
        self._data = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key):
        with self._lock:
            if key in self._data:
                self.hits += 1
                return self._data[key]
            self.misses += 1
        try:
            value = self.source.read(key)
        except Exception:
            value = None  # 缺陷2：回源失败被当成空值
        with self._lock:
            self._data[key] = value  # 缺陷1：无条件写回，可覆盖更新的值
        return value

    def set(self, key, value):
        self.source.write(key, value)
        with self._lock:
            self._data.pop(key, None)

    def delete(self, key):
        with self._lock:
            self._data.pop(key, None)
