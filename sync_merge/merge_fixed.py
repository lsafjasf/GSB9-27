"""确定性字段级合并实现（LWW + 版本向量 + 墓碑）。

决胜规则（全序，与到达顺序无关，见 MERGE_RULES.md）：
    1. 版本号（逻辑计数器）大者胜 —— 免疫时钟回拨；
    2. 版本号相同，时间戳大者胜；
    3. 时间戳也相同，来源标识字典序大者胜（缺失归一化为 ""）；
    4. 以上全部相同（同来源同序号的两次写入），按值的规范序列化
       字典序大者胜 —— 保证任何输入都有唯一确定的结果。

合并满足交换律、结合律、幂等律：任意端以任意顺序合并同一组更新，
最终的有效字段值与版本向量完全一致（收敛性）。
"""

import json
from dataclasses import dataclass, field

MISSING_SOURCE = ""  # 来源标识缺失时的归一化值


def value_key(value):
    """值的规范序列化，作为最终决胜键（可比较、与进程/平台无关）。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=repr)


@dataclass(frozen=True)
class Stamp:
    """一次写入的确定性排序戳。比较键为 (version, ts, source) 的全序。"""

    version: int
    ts: int
    source: str = MISSING_SOURCE

    def __post_init__(self):
        # 归一化：来源标识缺失（None）时统一为 ""，保证可比较。
        if self.source is None:
            object.__setattr__(self, "source", MISSING_SOURCE)

    def key(self):
        return (self.version, self.ts, self.source)


def newer_stamp(a, b):
    """返回两个 Stamp 中的胜者（全序最大者）；任一可为 None。"""
    if a is None:
        return b
    if b is None:
        return a
    return a if a.key() >= b.key() else b


def _entry_key(stamp, value):
    """一次写入（戳 + 值）的完整决胜键。"""
    return (stamp.key(), value_key(value))


def merge_version_vector(vv_a, vv_b):
    """版本向量合并：逐分量取最大。来源缺失已归一化，不会被丢弃。"""
    merged = dict(vv_a)
    for source, counter in vv_b.items():
        key = source if source is not None else MISSING_SOURCE
        if counter > merged.get(key, 0):
            merged[key] = counter
    return merged


@dataclass
class Record:
    """一条记录：字段级写入 + 记录级写入/删除（墓碑）+ 版本向量。"""

    fields: dict = field(default_factory=dict)        # name -> (Stamp, value)
    record_stamp: Stamp = None                        # 最近一次记录级写/删
    record_value: dict = None                         # 记录级写入的值；None+有戳=已删除
    version_vector: dict = field(default_factory=dict)

    # ---- 本地操作（生成更新） ----

    def _next_stamp(self, source, ts):
        source = source if source is not None else MISSING_SOURCE
        return Stamp(self.version_vector.get(source, 0) + 1, ts, source)

    def _bump(self, stamp):
        self.version_vector[stamp.source] = stamp.version

    def apply_field_update(self, name, value, source, ts):
        stamp = self._next_stamp(source, ts)
        self._bump(stamp)
        self.fields[name] = (stamp, value)

    def apply_record_update(self, values, source, ts):
        stamp = self._next_stamp(source, ts)
        self._bump(stamp)
        self.record_stamp = stamp
        self.record_value = dict(values)

    def apply_delete(self, source, ts):
        stamp = self._next_stamp(source, ts)
        self._bump(stamp)
        self.record_stamp = stamp
        self.record_value = None  # 墓碑

    # ---- 合并 ----

    def merge(self, other):
        """把 other 合并进自身。满足交换/结合/幂等，与顺序无关。"""
        self.version_vector = merge_version_vector(self.version_vector,
                                                   other.version_vector)
        # 记录级：按 (戳, 值) 全序取大者（删除墓碑也参与比较）
        if other.record_stamp is not None:
            if (self.record_stamp is None
                    or _entry_key(other.record_stamp, other.record_value)
                    > _entry_key(self.record_stamp, self.record_value)):
                self.record_stamp = other.record_stamp
                self.record_value = None if other.record_value is None \
                    else dict(other.record_value)
        # 字段级：逐字段按 (戳, 值) 全序取大者
        for name, (stamp, value) in other.fields.items():
            cur = self.fields.get(name)
            if cur is None or _entry_key(stamp, value) > _entry_key(*cur):
                self.fields[name] = (stamp, value)
        return self

    def clone(self):
        return Record(
            fields=dict(self.fields),
            record_stamp=self.record_stamp,
            record_value=None if self.record_value is None
            else dict(self.record_value),
            version_vector=dict(self.version_vector),
        )

    # ---- 有效视图 ----

    def effective_fields(self):
        """计算当前可见的字段值；记录已删除返回 None。

        字段级戳与记录级戳比较：字段戳更新则字段级值生效，否则取记录级值。
        """
        if self.record_stamp is not None and self.record_value is None:
            # 删除墓碑：只有比墓碑更新的字段级写入才可见
            visible = {name: value for name, (stamp, value) in self.fields.items()
                       if stamp.key() > self.record_stamp.key()}
            return visible if visible else None
        result = {}
        if self.record_value is not None:
            result.update(self.record_value)
        for name, (stamp, value) in self.fields.items():
            if self.record_stamp is None or stamp.key() > self.record_stamp.key():
                result[name] = value
        return result

    def state_fingerprint(self):
        """收敛性断言用：有效值 + 版本向量的规范化表示。"""
        eff = self.effective_fields()
        return (
            None if eff is None else tuple(sorted(eff.items())),
            tuple(sorted(self.version_vector.items())),
        )
