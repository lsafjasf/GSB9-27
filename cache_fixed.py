"""修复后的读穿缓存实现（仅标准库，线程安全）。

手段：
- 单飞（single-flight）：同一键同一时刻只允许一个回源，
  其余并发调用方等待该次回源完成，避免回源风暴。
- 版本屏障（version barrier / 写入屏障）：每次失效（set/delete）递增键版本号；
  回源开始前记录版本，写回时若版本已变化（期间发生过失效）则丢弃结果，
  防止在途的旧值响应覆盖新值。
- 失败处理：回源异常（含 TimeoutError）不写入缓存（不做负缓存），
  异常传播给当前所有等待者；下一次读取会重新回源，数据源恢复后立即可用。

一致性保证（单键）：
- 设某次失效操作 I 已完成，则任何在 I 之后开始的读取，
  其返回值对应的回源必然开始于 I 之后，因此不会返回比 I 更旧的值，
  也不会返回空值（除非数据源本身无此键，此时抛 KeyError）。
- 与写入并发的在途读取可能返回旧值，这符合线性化语义
  （该读取可被线性化到写入之前）。
"""

import threading


class _Inflight:
    __slots__ = ("done", "value", "error")

    def __init__(self):
        self.done = threading.Event()
        self.value = None
        self.error = None


class FixedCache:
    def __init__(self, source):
        self.source = source
        self._lock = threading.Lock()
        self._entries = {}    # key -> value
        self._versions = {}   # key -> int，失效时递增
        self._inflight = {}   # key -> _Inflight，进行中的回源
        self.hits = 0
        self.misses = 0

    def get(self, key):
        while True:
            with self._lock:
                if key in self._entries:
                    self.hits += 1
                    return self._entries[key]
                self.misses += 1
                version = self._versions.get(key, 0)
                inflight = self._inflight.get(key)
                if inflight is None:
                    inflight = _Inflight()
                    self._inflight[key] = inflight
                    leader = True
                else:
                    leader = False

            if leader:
                try:
                    inflight.value = self.source.read(key)
                except Exception as exc:  # 含 TimeoutError
                    inflight.error = exc
                with self._lock:
                    # 写入屏障：回源期间发生过失效则丢弃结果
                    if inflight.error is None and self._versions.get(key, 0) == version:
                        self._entries[key] = inflight.value
                    del self._inflight[key]
                    inflight.done.set()
                if inflight.error is not None:
                    raise inflight.error
                return inflight.value

            inflight.done.wait()
            if inflight.error is not None:
                raise inflight.error
            # 跟随者循环重查：若回源期间发生过失效，leader 未写回，
            # 需要重新回源，保证返回值不旧于最近一次已完成的失效。

    def set(self, key, value):
        self.source.write(key, value)
        self._invalidate(key)

    def delete(self, key):
        self._invalidate(key)

    def _invalidate(self, key):
        with self._lock:
            self._versions[key] = self._versions.get(key, 0) + 1
            self._entries.pop(key, None)
