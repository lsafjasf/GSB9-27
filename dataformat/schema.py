"""格式版本定义：逐版本的必填字段、默认值与迁移步骤。

版本演进：
  v1 -> v2: timeout 改名 timeout_ms（秒->毫秒）；server 新增嵌套字段 retries
  v2 -> v3: name 删除；新增 tags
"""

def _ms_to_seconds(ms):
    return ms // 1000 if ms % 1000 == 0 else ms / 1000

# 每个版本的必填字段（点分路径）。缺失即抛 MissingFieldError。
REQUIRED = {
    1: ["name", "timeout", "server.host"],
    2: ["name", "timeout_ms", "server.host"],
    3: ["timeout_ms", "server.host"],
}

# 每个版本的可选字段默认值（点分路径）。
# 缺失时填充默认值，但必须记入 _meta.defaulted 显式标记。
DEFAULTS = {
    1: {"server.port": 80},
    2: {"server.port": 80, "server.retries": 3},
    3: {"server.port": 80, "server.retries": 3, "tags": list},
}

# 升级步骤：renames {旧路径: (新路径, 转换函数)}，adds {路径: 默认值}，
# removes [路径]（删除时归档到 _meta.removed，供降级恢复）。
UPGRADES = {
    (1, 2): {
        "renames": {"timeout": ("timeout_ms", lambda seconds: seconds * 1000)},
        "adds": {"server.retries": 3},
        "removes": [],
    },
    (2, 3): {
        "renames": {},
        "adds": {"tags": list},
        "removes": ["name"],
    },
}

# 降级步骤：新版本的额外字段对旧版本是"未知字段"，按策略原样保留；
# 被删除的字段优先从 _meta.removed 归档恢复。
DOWNGRADES = {
    (3, 2): {
        "renames": {},
        "restores": ["name"],   # 从 _meta.removed 恢复；归档缺失且必填则报错
    },
    (2, 1): {
        "renames": {"timeout_ms": ("timeout", _ms_to_seconds)},
        "restores": [],
    },
}
