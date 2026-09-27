""" buggy 版共享夹具：多个用例共用同一份可变环境，互相污染。

污染源（全部为模块级可变状态，写入方见注释）：
  1. TMP_DIR    共享临时目录      —— 任何调用 workspace() 并写文件的用例
  2. REGISTRY   全局注册表 dict   —— 任何调用 register() 的用例
  3. COUNTER    全局共享计数      —— 任何调用 bump() 的用例
  4. os.environ 进程级环境变量    —— 任何调用 set_mode() 的用例
  5. CACHE      全局缓存 dict     —— 任何调用 cached_compute() 的用例
"""
import os
import tempfile

TMP_DIR = os.path.join(tempfile.gettempdir(), "gsb9_shared_tmp")
os.makedirs(TMP_DIR, exist_ok=True)

REGISTRY = {}
COUNTER = {"value": 0}
CACHE = {}


def workspace():
    return TMP_DIR


def register(name, value):
    REGISTRY[name] = value


def bump(n=1):
    COUNTER["value"] += n
    return COUNTER["value"]


def set_mode(mode):
    os.environ["APP_MODE"] = mode


def get_mode():
    return os.environ.get("APP_MODE")


def cached_compute(key, fn):
    if key not in CACHE:
        CACHE[key] = fn()
    return CACHE[key]
