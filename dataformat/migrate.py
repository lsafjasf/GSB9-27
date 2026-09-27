"""修复版迁移引擎。

设计原则：
  1. 字段缺失与字段值为默认值严格区分：
     - 必填字段缺失 -> 抛 MissingFieldError；
     - 可选字段缺失 -> 填默认值，并把路径记入 _meta.defaulted 显式标记。
  2. 未知字段原样保留：迁移只动 schema 声明的 key，其余一律不碰，
     写回时自然带回（升级/降级均如此）。
  3. 删除字段不丢数据：removes 的字段归档到 _meta.removed，降级时恢复。
"""

import copy

from .errors import MigrationError, MissingFieldError, UnknownVersionError
from .schema import DEFAULTS, DOWNGRADES, REQUIRED, UPGRADES

META_KEY = "_meta"


# ---------- 点分路径工具 ----------

def _split(path):
    return path.split(".")


def _has(data, path):
    node = data
    for part in _split(path):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def _get(data, path):
    node = data
    for part in _split(path):
        node = node[part]
    return node


def _set(data, path, value):
    node = data
    parts = _split(path)
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _del(data, path):
    node = data
    parts = _split(path)
    for part in parts[:-1]:
        node = node[part]
    del node[parts[-1]]


# ---------- 校验 ----------

def _validate_required(data, version):
    for path in REQUIRED[version]:
        if not _has(data, path):
            raise MissingFieldError(path, version)


def _fill_defaults(data, version, defaulted):
    for path, default in DEFAULTS[version].items():
        if not _has(data, path):
            value = default() if callable(default) else copy.deepcopy(default)
            _set(data, path, value)
            defaulted.append(path)


# ---------- 升级 ----------

def _apply_upgrade_step(data, step, meta):
    defaulted = []
    for old_path, (new_path, transform) in step["renames"].items():
        if _has(data, old_path):
            _set(data, new_path, transform(_get(data, old_path)))
            _del(data, old_path)
        # 旧 key 缺失时不在此报错：目标版本的必填校验会显式抛出。
    for path, default in step["adds"].items():
        if not _has(data, path):
            value = default() if callable(default) else copy.deepcopy(default)
            _set(data, path, value)
            defaulted.append(path)
    for path in step["removes"]:
        if _has(data, path):
            meta.setdefault("removed", {})[path] = _get(data, path)
            _del(data, path)
    return defaulted


def upgrade(data, from_version, to_version):
    """把 data 从 from_version 逐级升级到 to_version，返回新字典。

    - 输入不合法（必填缺失）抛 MissingFieldError；
    - 结果带 _meta: {version, source_version, defaulted, removed}；
    - 未知字段原样保留。
    """
    if from_version > to_version:
        raise MigrationError("upgrade() 只用于升级，降级请用 downgrade()")
    if from_version == to_version:
        return copy.deepcopy(data)
    if not all((v, v + 1) in UPGRADES for v in range(from_version, to_version)):
        raise UnknownVersionError(f"不支持的升级路径: v{from_version} -> v{to_version}")

    out = copy.deepcopy(data)
    out.pop(META_KEY, None)
    _validate_required(out, from_version)

    meta = {"source_version": from_version}
    defaulted = []
    for v in range(from_version, to_version):
        defaulted.extend(_apply_upgrade_step(out, UPGRADES[(v, v + 1)], meta))
    _fill_defaults(out, to_version, defaulted)
    _validate_required(out, to_version)

    meta["defaulted"] = sorted(defaulted)
    out["version"] = to_version
    out[META_KEY] = meta
    return out


# ---------- 降级 ----------

def _apply_downgrade_step(data, step, meta):
    removed = meta.get("removed", {})
    for old_path, (new_path, transform) in step["renames"].items():
        if _has(data, old_path):
            _set(data, new_path, transform(_get(data, old_path)))
            _del(data, old_path)
    for path in step["restores"]:
        if path in removed:
            _set(data, path, removed.pop(path))
        # 归档缺失时不在此报错：目标版本必填校验显式抛出。


def downgrade(data, from_version, to_version):
    """把 data 从 from_version 逐级降级到 to_version（供旧代码读取）。

    - 被删除的字段优先从 _meta.removed 归档恢复；
    - 归档缺失且目标版本必填 -> MissingFieldError（显式报错，不填假值）；
    - 新版本多出的字段对旧版本是未知字段，原样保留。
    """
    if from_version < to_version:
        raise MigrationError("downgrade() 只用于降级，升级请用 upgrade()")
    if from_version == to_version:
        return copy.deepcopy(data)
    if not all((v, v - 1) in DOWNGRADES for v in range(from_version, to_version, -1)):
        raise UnknownVersionError(f"不支持的降级路径: v{from_version} -> v{to_version}")

    out = copy.deepcopy(data)
    meta = out.pop(META_KEY, {})
    for v in range(from_version, to_version, -1):
        _apply_downgrade_step(out, DOWNGRADES[(v, v - 1)], meta)
    _validate_required(out, to_version)
    out["version"] = to_version
    if meta.get("removed"):
        out[META_KEY] = {"removed": meta["removed"]}
    return out
