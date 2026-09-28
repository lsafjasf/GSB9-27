"""迁移报告与往返一致性核对。

报告由 (输入, 迁移结果) 派生，逐字段记录：
  - renamed:           改名/转换（来源路径、目标路径、转换函数、前后值）
  - removed:           删除（值归档到 _meta.removed，降级可恢复）
  - defaulted:         缺失被默认值填充（来源为 schema 默认值，非用户配置）
  - kept:              schema 已知字段，原样保留
  - preserved_unknown: 未知字段，原样保留
  - version_bump:      版本号推进

verify_report() 独立于生成逻辑，对照输入与迁移结果逐条复核报告，
任何一条与实际数据不符即抛 ReportMismatchError —— 报告可逐条核对。

roundtrip_diff() 执行 升级 -> 降级 往返，列出往返后与原文档的差异，
用于说明「往返是超集而非严格相等」的具体字段。
"""

from .errors import MigrationError
from .migrate import META_KEY, downgrade, upgrade
from .schema import DEFAULTS, REQUIRED, UPGRADES

RESERVED_KEYS = {"version", META_KEY}


class ReportMismatchError(MigrationError):
    """报告条目与实际迁移结果不一致。"""


# ---------- 路径工具 ----------

def _leaf_paths(data, prefix=""):
    """递归展开 dict，返回 {点分路径: 叶子值}。list 视为叶子值。"""
    leaves = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            leaves.update(_leaf_paths(value, path))
        else:
            leaves[path] = value
    return leaves


def _rename_map(from_version, to_version):
    """合并升级链上所有改名步骤: {旧路径: (新路径, 转换函数)}。"""
    renames = {}
    for v in range(from_version, to_version):
        renames.update(UPGRADES[(v, v + 1)]["renames"])
    return renames


def _known_paths(from_version, to_version):
    """升级链涉及版本中 schema 声明过的全部路径（含改名的两端）。"""
    known = set()
    for v in range(from_version, to_version + 1):
        known.update(REQUIRED.get(v, ()))
        known.update(DEFAULTS.get(v, ()))
    for old_path, (new_path, _transform) in _rename_map(from_version, to_version).items():
        known.add(old_path)
        known.add(new_path)
    for v in range(from_version, to_version):
        step = UPGRADES[(v, v + 1)]
        known.update(step["adds"])
        known.update(step["removes"])
    return known


# ---------- 报告生成 ----------

def build_report(input_data, from_version, to_version, result=None):
    """执行（或校验已执行的）升级，并生成逐字段迁移报告。

    result 为 None 时内部调用 upgrade()；传入已有结果时，
    报告仍从 (input_data, result) 派生，可用 verify_report 复核一致性。
    """
    if result is None:
        result = upgrade(input_data, from_version, to_version)

    meta = result.get(META_KEY, {})
    removed_archive = meta.get("removed", {})
    defaulted_paths = set(meta.get("defaulted", ()))
    renames = _rename_map(from_version, to_version)
    known = _known_paths(from_version, to_version)

    input_leaves = {
        path: value
        for path, value in _leaf_paths(input_data).items()
        if path.split(".")[0] not in RESERVED_KEYS
    }
    result_leaves = {
        path: value
        for path, value in _leaf_paths(result).items()
        if path.split(".")[0] not in RESERVED_KEYS
    }

    entries = [{
        "path": "version",
        "action": "version_bump",
        "value_before": input_data.get("version"),
        "value_after": result.get("version"),
    }]
    rename_targets = set()

    for path in sorted(input_leaves):
        value = input_leaves[path]
        if path in renames:
            new_path, transform = renames[path]
            rename_targets.add(new_path)
            entries.append({
                "path": path,
                "action": "renamed",
                "to": new_path,
                "transform": getattr(transform, "__name__", repr(transform)),
                "source": "input",
                "value_before": value,
                "value_after": result_leaves.get(new_path),
            })
        elif path in removed_archive:
            entries.append({
                "path": path,
                "action": "removed",
                "source": "input",
                "archived_to": f"{META_KEY}.removed.{path}",
                "value_before": value,
            })
        elif path in result_leaves and result_leaves[path] == value:
            action = "kept" if path in known else "preserved_unknown"
            entries.append({
                "path": path,
                "action": action,
                "source": "input",
                "value_before": value,
                "value_after": value,
            })
        else:
            entries.append({
                "path": path,
                "action": "changed",
                "source": "input",
                "value_before": value,
                "value_after": result_leaves.get(path),
            })

    for path in sorted(result_leaves):
        if path in input_leaves or path in rename_targets:
            continue
        if path in defaulted_paths:
            entries.append({
                "path": path,
                "action": "defaulted",
                "source": "schema_default",
                "value_after": result_leaves[path],
            })
        else:
            entries.append({
                "path": path,
                "action": "added",
                "source": "unknown",
                "value_after": result_leaves[path],
            })

    return {
        "from_version": from_version,
        "to_version": to_version,
        "fields": entries,
        "summary": {
            action: sum(1 for e in entries if e["action"] == action)
            for action in (
                "renamed", "removed", "defaulted",
                "kept", "preserved_unknown", "changed", "added", "version_bump",
            )
        },
    }


# ---------- 报告核对 ----------

def _fail(path, message):
    raise ReportMismatchError(f"报告与迁移结果不一致 @{path}: {message}")


def verify_report(report, input_data, result):
    """对照 input_data 与 result 逐条复核报告，不一致即抛 ReportMismatchError。

    除逐条校验外还做完备性检查：输入与结果的每个叶子字段都必须被
    恰好一条报告条目覆盖，防止漏报。
    """
    input_leaves = _leaf_paths(input_data)
    result_leaves = _leaf_paths(result)
    renames = _rename_map(report["from_version"], report["to_version"])
    removed_archive = result.get(META_KEY, {}).get("removed", {})
    defaulted_paths = set(result.get(META_KEY, {}).get("defaulted", ()))

    covered_input, covered_output = set(), set()

    for entry in report["fields"]:
        path, action = entry["path"], entry["action"]
        if action == "version_bump":
            if result.get("version") != entry["value_after"]:
                _fail(path, f"版本号应为 {entry['value_after']!r}，实际 {result.get('version')!r}")
            continue
        if action == "renamed":
            new_path, transform = renames[path]
            if new_path != entry["to"]:
                _fail(path, f"改名目标应为 {new_path!r}，报告为 {entry['to']!r}")
            if path not in input_leaves:
                _fail(path, "输入中不存在该字段")
            expect = transform(input_leaves[path])
            if result_leaves.get(new_path) != expect:
                _fail(path, f"结果 {new_path} 应为 {expect!r}，实际 {result_leaves.get(new_path)!r}")
            covered_input.add(path)
            covered_output.add(new_path)
        elif action == "removed":
            if path in result_leaves:
                _fail(path, "该字段应已删除，但仍存在于结果中")
            if removed_archive.get(path) != input_leaves.get(path):
                _fail(path, "归档值与输入值不一致")
            covered_input.add(path)
        elif action in ("kept", "preserved_unknown"):
            if path not in result_leaves or result_leaves[path] != input_leaves.get(path):
                _fail(path, "结果中该字段缺失或值被改动")
            covered_input.add(path)
            covered_output.add(path)
        elif action == "defaulted":
            if path in input_leaves:
                _fail(path, "输入中已存在该字段，不应标记为默认值填充")
            if path not in defaulted_paths:
                _fail(path, "该字段未记入 _meta.defaulted")
            if result_leaves.get(path) != entry["value_after"]:
                _fail(path, "结果值与报告记录不一致")
            covered_output.add(path)
        elif action in ("changed", "added"):
            if result_leaves.get(path) != entry["value_after"]:
                _fail(path, "结果值与报告记录不一致")
            covered_output.add(path)
            if action == "changed":
                covered_input.add(path)
        else:
            _fail(path, f"未知的报告动作 {action!r}")

    business_input = {p for p in input_leaves if p.split(".")[0] not in RESERVED_KEYS}
    business_output = {p for p in result_leaves if p.split(".")[0] not in RESERVED_KEYS}
    missing_in = business_input - covered_input
    missing_out = business_output - covered_output
    if missing_in:
        _fail(sorted(missing_in)[0], "输入字段未被任何报告条目覆盖")
    if missing_out:
        _fail(sorted(missing_out)[0], "结果字段未被任何报告条目覆盖")
    return True


# ---------- 往返一致性 ----------

def roundtrip_diff(data, from_version, via_version):
    """升级 from_version -> via_version 再降级回来，返回 (往返结果, 差异列表)。

    差异只可能是「超集」方向：往返结果比原文档多出的字段，是升级链上
    较新版本引入的默认值（如 server.retries、tags），降级时按未知字段
    策略保留，因此往返不保证与原文档严格相等。
    """
    up = upgrade(data, from_version, via_version)
    back = downgrade(up, via_version, from_version)

    original_leaves = {
        path: value
        for path, value in _leaf_paths(data).items()
        if path.split(".")[0] not in RESERVED_KEYS
    }
    back_leaves = {
        path: value
        for path, value in _leaf_paths(back).items()
        if path.split(".")[0] not in RESERVED_KEYS
    }

    diffs = []
    for path in sorted(back_leaves):
        if path not in original_leaves:
            diffs.append({
                "path": path,
                "kind": "added",
                "roundtrip_value": back_leaves[path],
                "reason": "较新版本引入的字段，降级时按未知字段策略保留（超集）",
            })
        elif back_leaves[path] != original_leaves[path]:
            diffs.append({
                "path": path,
                "kind": "changed",
                "original_value": original_leaves[path],
                "roundtrip_value": back_leaves[path],
            })
    for path in sorted(original_leaves):
        if path not in back_leaves:
            diffs.append({
                "path": path,
                "kind": "removed",
                "original_value": original_leaves[path],
            })
    return back, diffs
