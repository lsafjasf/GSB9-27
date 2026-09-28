"""有缺陷的共享夹具：所有用例共用同一份可变环境，且不做任何清理。

污染源（被跨用例共享的可变状态）：
  1. TMPDIR   —— 进程级唯一临时目录，所有用例往同一个目录写文件
  2. REGISTRY —— 模块级全局注册表 dict，各用例随意写入键
  3. COUNTER  —— 模块级共享计数器，setup 时自增，永不复位
  4. os.environ —— 进程级环境变量，用例直接写入且泄漏
  5. LOOKUP_CACHE —— 模块级 memoization 缓存 dict，业务函数命中/填充后
                     永不失效，后续用例在同一进程里直接读到上一条用例
                     缓存下来的旧结果
"""
import os
import tempfile

TMPDIR = tempfile.mkdtemp(prefix="shared_env_")   # 污染源 1：共享临时目录
REGISTRY = {}                                      # 污染源 2：全局注册表
COUNTER = {"n": 0}                                 # 污染源 3：共享计数
ENV_KEY = "GSB_FIXTURE_PROFILE"                    # 污染源 4：环境变量键
LOOKUP_CACHE = {}                                  # 污染源 5：模块级缓存


def cached_lookup(key, value):
    """典型的模块级 memoization：命中则复用旧值，未命中则永久缓存。

    与 REGISTRY 的区别：它不是显式"登记处"，而是业务上的只读缓存——
    用例通常意识不到它的存在，更不会去清理它，因此最容易被跨用例复用。
    """
    if key not in LOOKUP_CACHE:
        LOOKUP_CACHE[key] = value
    return LOOKUP_CACHE[key]


class SharedFixture:
    """缺陷点：setup 只增不清，teardown 是空操作。

    - 不写临时目录隔离：所有用例读写同一个 TMPDIR
    - 不快照/还原 REGISTRY 与 COUNTER
    - 不备份/还原 os.environ
    - 不清空 LOOKUP_CACHE
    - teardown 不做任何事，失败路径（异常/跳过）同样残留
    """

    def setup(self):
        REGISTRY.setdefault("initialized", True)
        COUNTER["n"] += 1
        self.workdir = TMPDIR

    def teardown(self):
        pass  # 缺陷：什么都不还原
