"""修复后的夹具：按用例隔离 + 快照还原，失败路径同样清理，并发安全。

修复策略（对应 fixture_buggy.py 的五个污染源）：
  1. 临时目录：每个用例 tempfile.mkdtemp 独立目录，退出时 shutil.rmtree
  2. 注册表：  每个用例独立的 fx.registry 实例，不再共享模块级 dict
  3. 计数器：  每个用例独立的 fx.counter，从 0 开始
  4. 缓存：    每个用例独立的 fx.lookup_cache，模块级 memoization 不复存在
  5. 环境变量：进程级资源无法按线程隔离，采用
     - fx.set_env() 记录原值，退出时精确还原（含"原本不存在"的情况）
     - uses_env=True 的用例在 ENV_LOCK 下串行化，避免并发互相观测

清理保证：所有还原逻辑在 __exit__/close 的 try/finally 中执行，
用例抛异常（RuntimeError 等）或跳过（unittest.SkipTest）时同样生效。
"""
import os
import shutil
import tempfile
import threading

_MISSING = object()


class IsolatedFixture:
    ENV_LOCK = threading.RLock()  # 进程级环境变量的串行化锁

    def __init__(self, name, uses_env=False):
        self.name = name
        self.uses_env = uses_env
        # 隔离状态：每个用例一份，绝不共享
        self.registry = {}
        self.counter = 0
        self.lookup_cache = {}
        self.tmpdir = None
        self._env_originals = {}
        self._lock_held = False
        self._closed = False

    def __enter__(self):
        if self.uses_env:
            self.ENV_LOCK.acquire()
            self._lock_held = True
        self.tmpdir = tempfile.mkdtemp(prefix="fx_%s_" % self.name)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False  # 不吞异常

    def close(self):
        """还原环境；幂等，且在异常/跳过路径同样被调用。"""
        if self._closed:
            return
        self._closed = True
        try:
            if self.tmpdir is not None:
                shutil.rmtree(self.tmpdir, ignore_errors=True)
            for key, old in self._env_originals.items():
                if old is _MISSING:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old
        finally:
            if self._lock_held:
                self._lock_held = False
                self.ENV_LOCK.release()

    def set_env(self, key, value):
        """写环境变量并记录原值，供 close() 精确还原。"""
        if key not in self._env_originals:
            self._env_originals[key] = os.environ.get(key, _MISSING)
        os.environ[key] = value

    def cached_lookup(self, key, value):
        """每用例独立的 memoization：缓存随夹具一起销毁，绝不跨用例复用。"""
        if key not in self.lookup_cache:
            self.lookup_cache[key] = value
        return self.lookup_cache[key]
