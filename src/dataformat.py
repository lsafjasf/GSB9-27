"""分版本数据格式：显式、无损的逐版本迁移。

设计原则：
- 字段缺失与字段值为默认值严格区分：
  * 有默认值的新增字段：填默认值，并把路径记入报告与文档内的
    ``__meta__.defaults_applied``，下游可据此识别"这不是真实数据"。
  * 无默认值的新增字段（REQUIRED）：必须通过 ``fill`` 显式提供，
    否则抛出 MissingFieldError，绝不用默认值掩盖。
  * 本应存在的字段（keeps / 改名源）缺失：抛 MissingFieldError。
- 未知字段（不属于任何版本 schema，如未来版本字段、用户自定义字段）
  在每一级迁移中原样保留，写回时不丢失。
- 显式删除的字段会被移除，但路径记入报告（report.deleted）。
"""

import copy

CURRENT_VERSION = 3


class MigrationError(Exception):
    """迁移过程中的通用错误。"""


class MissingFieldError(MigrationError):
    """字段缺失且没有可用的默认值/填充值。"""

    def __init__(self, path, from_version, to_version):
        self.path = path
        self.from_version = from_version
        self.to_version = to_version
        super().__init__(
            "字段缺失且无默认值: %r (v%d -> v%d)。"
            "请通过 fill={%r: ...} 显式提供该字段。"
            % (path, from_version, to_version, path)
        )


class UnsupportedVersionError(MigrationError):
    pass


class _Required:
    def __repr__(self):
        return "REQUIRED"


#: 新增字段的默认值取 REQUIRED 时，表示该字段没有默认值，
#: 必须由旧数据、或调用方通过 fill 显式提供，否则报错。
REQUIRED = _Required()

_MISS = object()

# 每一级迁移的显式声明。字段对照表见 MIGRATION.md。
STEPS = {
    (1, 2): {
        "keeps": ["name"],
        "renames": {"nick": "nickname"},
        "adds": {"email": None},
        "deletes": [],
        "nested": {
            "settings": {
                "keeps": ["theme", "font_size"],
                "renames": {},
                "adds": {"language": "en"},
                "deletes": [],
                "nested": {},
            }
        },
    },
    (2, 3): {
        "keeps": ["name", "nickname"],
        "renames": {"email": "contact_email"},
        "adds": {"tags": []},
        "deletes": [],
        "nested": {
            "settings": {
                "keeps": ["theme", "language"],
                "renames": {},
                "adds": {"timezone": REQUIRED},
                "deletes": ["font_size"],
                "nested": {},
            }
        },
    },
}


class MigrationReport:
    """一次迁移的完整审计记录。"""

    def __init__(self):
        self.renamed = []           # [(old_path, new_path)]
        self.defaults_applied = []  # [path] 值来自默认值，不是真实数据
        self.filled = []            # [path] 值来自调用方 fill
        self.deleted = []           # [path] 按 schema 显式删除
        self.preserved = []         # [path] 未知字段，原样保留

    def __repr__(self):
        return (
            "MigrationReport(renamed=%r, defaults_applied=%r, filled=%r, "
            "deleted=%r, preserved=%r)"
            % (self.renamed, self.defaults_applied, self.filled,
               self.deleted, self.preserved)
        )


def _join(path, key):
    return "%s.%s" % (path, key) if path else key


def _apply_step(data, step, path, report, fill, from_version, to_version):
    if not isinstance(data, dict):
        raise MigrationError("路径 %r 处应为对象，实际是 %r"
                             % (path or "<root>", type(data).__name__))
    out = {}
    consumed = set()

    # 1) 保留字段：本应存在，缺失即报错（不得用默认值掩盖）。
    for key in step["keeps"]:
        if key not in data:
            raise MissingFieldError(_join(path, key), from_version, to_version)
        out[key] = data[key]
        consumed.add(key)

    # 2) 改名字段：旧名 -> 新名；新旧名都不存在即报错。
    for old, new in step["renames"].items():
        if old in data:
            out[new] = data[old]
            consumed.add(old)
            report.renamed.append((_join(path, old), _join(path, new)))
        elif new in data:
            out[new] = data[new]  # 已是新名（例如重复迁移），原样保留
            consumed.add(new)
        else:
            raise MissingFieldError(_join(path, new), from_version, to_version)

    # 3) 新增字段：区分"显式默认值"与"必须显式提供"。
    for key, default in step["adds"].items():
        full = _join(path, key)
        if key in data:
            # 数据中已存在（未知字段恰好同名）：视为真实值，原样保留，
            # 不记入 defaults_applied。
            out[key] = data[key]
            consumed.add(key)
            continue
        supplied = fill.get(full, _MISS) if fill else _MISS
        if supplied is not _MISS:
            out[key] = supplied
            report.filled.append(full)
        elif default is REQUIRED:
            raise MissingFieldError(full, from_version, to_version)
        else:
            out[key] = copy.deepcopy(default)
            report.defaults_applied.append(full)

    # 4) 显式删除的字段：移除并记录。
    for key in step["deletes"]:
        if key in data:
            consumed.add(key)
            report.deleted.append(_join(path, key))

    # 5) 嵌套结构：递归迁移，而不是整体重建。
    for key, substep in step["nested"].items():
        if key not in data:
            raise MissingFieldError(_join(path, key), from_version, to_version)
        consumed.add(key)
        out[key] = _apply_step(data[key], substep, _join(path, key),
                               report, fill, from_version, to_version)

    # 6) 未知字段：原样保留（升级链上任何一级都不得丢弃）。
    for key, value in data.items():
        if key in consumed:
            continue
        out[key] = value
        report.preserved.append(_join(path, key))

    return out


def migrate(data, from_version, to_version, fill=None):
    """把数据从 from_version 逐级迁移到 to_version（仅支持升级）。

    返回 (新数据, MigrationReport)。数据中的 "version" 键由
    migrate_document 处理，这里只迁移负载字段。
    """
    if from_version == to_version:
        return copy.deepcopy(data), MigrationReport()
    if from_version > to_version:
        raise UnsupportedVersionError(
            "不支持从 v%d 降级到 v%d。降级请使用 compatibility_report "
            "评估影响，并由旧代码按 reader_view/merge_reader_write 读写。"
            % (from_version, to_version)
        )
    report = MigrationReport()
    current = copy.deepcopy(data)
    for version in range(from_version, to_version):
        step = STEPS.get((version, version + 1))
        if step is None:
            raise UnsupportedVersionError(
                "没有 v%d -> v%d 的迁移定义" % (version, version + 1))
        current = _apply_step(current, step, "", report, fill,
                              version, version + 1)
    return current, report


def migrate_document(doc, to_version=CURRENT_VERSION, fill=None):
    """迁移完整文档（含 "version" 键），返回 (新文档, MigrationReport)。

    若迁移过程中应用了默认值，会在文档内写入
    ``__meta__.defaults_applied``，使"该值是默认值而非真实数据"
    这一事实随文档持久化，下游读取时可以显式识别。
    """
    if "version" not in doc:
        raise MigrationError("文档缺少 'version' 键")
    from_version = doc["version"]
    payload = {k: v for k, v in doc.items() if k != "version"}
    migrated, report = migrate(payload, from_version, to_version, fill=fill)
    migrated["version"] = to_version
    if report.defaults_applied:
        meta = migrated.setdefault("__meta__", {})
        existing = meta.get("defaults_applied", [])
        meta["defaults_applied"] = existing + list(report.defaults_applied)
    return migrated, report


# ---------------------------------------------------------------------------
# 降级（新版本数据被旧代码读取）
# ---------------------------------------------------------------------------

def _fields_known_at(version):
    """返回某版本代码认识的字段路径集合（不含未知字段）。"""
    fields = {1: {"name", "nick", "settings.theme", "settings.font_size"}}
    for v in range(1, version):
        step = STEPS[(v, v + 1)]
        known = set(fields[v])
        for old, new in step["renames"].items():
            known.discard(old)
            known.add(new)
        known |= set(step["adds"])
        known -= set(step["deletes"])
        known |= set(step["keeps"])
        for nest, sub in step["nested"].items():
            prefix = nest + "."
            for old, new in sub["renames"].items():
                known.discard(prefix + old)
                known.add(prefix + new)
            known |= {prefix + k for k in sub["adds"]}
            known -= {prefix + k for k in sub["deletes"]}
            known |= {prefix + k for k in sub["keeps"]}
        fields[v + 1] = known
    return fields[version]


def compatibility_report(reader_version, data_version):
    """旧版本代码读取新版本数据时的影响评估（机器可读）。

    返回 dict：
    - missing_for_reader: 数据里已经没有、但旧代码期望的字段
      （被改名或删除）。旧代码必须把它们当作"缺失"显式处理，
      不得用默认值冒充。
    - invisible_to_reader: 数据里有、但旧代码不认识的字段。
      只要旧代码写回时保留未知字段（merge_reader_write），就不会丢失。
    """
    if reader_version >= data_version:
        return {"missing_for_reader": [], "invisible_to_reader": []}
    reader_fields = _fields_known_at(reader_version)
    data_fields = _fields_known_at(data_version)
    return {
        "missing_for_reader": sorted(reader_fields - data_fields),
        "invisible_to_reader": sorted(data_fields - reader_fields),
    }


def reader_view(doc, reader_version):
    """模拟旧版本代码的读取：只返回该版本认识的顶层键。

    "version" 与 "__meta__" 始终可见。旧代码对不认识的字段不可见，
    但只要用 merge_reader_write 写回，这些字段不会丢失。
    """
    known_top = {f.split(".", 1)[0] for f in _fields_known_at(reader_version)}
    return {k: v for k, v in doc.items()
            if k in known_top or k in ("version", "__meta__")}


def merge_reader_write(original_doc, reader_version, updates):
    """旧版本代码写回：合并其修改，原样保留它不认识的字段。"""
    known_top = {f.split(".", 1)[0] for f in _fields_known_at(reader_version)}
    merged = dict(original_doc)
    for key, value in updates.items():
        if key not in known_top and key not in ("version", "__meta__"):
            raise MigrationError(
                "v%d 代码不应写入未知字段 %r" % (reader_version, key))
        merged[key] = value
    return merged
