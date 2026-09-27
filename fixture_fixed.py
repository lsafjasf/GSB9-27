"""修复版夹具：每个用例一份隔离环境，失败/跳过路径也保证清理，并发安全。

修复策略（对应 buggy 版的 5 个污染源）：
  1. 临时目录  —— 每用例 tempfile.TemporaryDirectory，close() 时必清理
  2. 注册表    —— 每用例一份独立 dict，用例结束即丢弃
  3. 共享计数  —— 每用例一份独立计数器
  4. 环境变量  —— 不再写进程级 os.environ，改为夹具内的隔离视图（overlay），
                 进程全局状态零接触，天然线程安全
  5. 缓存      —— 每用例一份独立 dict

并发安全：当前夹具实例存放在 contextvars.ContextVar 中，
不同线程/任务各自绑定自己的实例，互不可见。
清理保证：close() 幂等，且通过 __exit__ / addCleanup 在异常与 SkipTest 路径同样执行。
"""
from __future__ import annotations

import contextvars
import os
import tempfile
import unittest

_current: contextvars.ContextVar["Fixture | None"] = contextvars.ContextVar(
    "gsb9_fixture", default=None
)


class Fixture:
    """一份完全隔离的用例环境。所有状态均为实例属性，进程内零共享。"""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gsb9_iso_")
        self.registry: dict[str, str] = {}
        self.counter = 0
        self.env: dict[str, str] = {}
        self.cache: dict[str, object] = {}
        self._token: contextvars.Token | None = None
        self._closed = False

    # ---- 生命周期 ----------------------------------------------------
    def __enter__(self) -> "Fixture":
        self._token = _current.set(self)
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False  # 不吞异常，清理后照常抛出

    def close(self) -> None:
        """幂等清理：先删临时目录，再解绑 contextvar；异常路径同样会走到。"""
        if self._closed:
            return
        self._closed = True
        try:
            self._tmp.cleanup()
        finally:
            if self._token is not None:
                _current.reset(self._token)
                self._token = None

    # ---- 隔离服务（与 buggy 版同名，便于对照） ------------------------
    def workspace(self) -> str:
        return self._tmp.name

    def register(self, name: str, value: str) -> None:
        self.registry[name] = value

    def bump(self, n: int = 1) -> int:
        self.counter += n
        return self.counter

    def set_env(self, key: str, value: str) -> None:
        self.env[key] = value

    def getenv(self, key: str, default=None):
        return self.env.get(key, os.environ.get(key, default))

    def cached_compute(self, key, fn):
        if key not in self.cache:
            self.cache[key] = fn()
        return self.cache[key]


def current() -> Fixture:
    fx = _current.get()
    if fx is None:
        raise RuntimeError("no active fixture in this context")
    return fx


class IsolatedTestCase(unittest.TestCase):
    """unittest 基类：每个用例自动获得全新 Fixture。

    addCleanup 在以下路径都会执行：断言失败、未捕获异常、SkipTest
    （包括 setUp 内注册 cleanup 之后再抛错的情况）。
    """

    def setUp(self) -> None:
        self.fixture = Fixture()
        self.fixture.__enter__()
        self.addCleanup(self.fixture.close)
