"""多端同步的确定性字段/记录合并实现（仅标准库）。

决胜规则（全序、确定、可解释），按优先级依次比较：
  1. version   —— Lamport 逻辑时钟（写入时取本端已知最大版本 +1，
                  因此因果上更晚的写入 version 一定更大）。免疫时钟回拨。
  2. timestamp —— 写者 wall-clock，仅作次级参考。
  3. source    —— 来源标识字符串序；缺失时视为 ""（排最前，即最小）。
  4. 删除优先  —— 记录级比较时，以上全同则墓碑（删除）胜过普通写入。
  5. value     —— 值的规范序列化（JSON, sort_keys）字典序，兜底决胜；
                  即使两条更新的来源都缺失、时间戳与版本全同，结果依然确定。

由于该顺序是全序，合并等价于对每个字段/记录取 max，满足交换律、结合律、
幂等律，因此任意端以任意顺序合并同一组更新，最终的字段值与版本向量完全
一致（收敛）。

删除语义：删除是带 Meta 的墓碑（tombstone），与更新走同一套决胜规则；
墓碑支配所有字段时记录视为已删除，存在比墓碑更新的字段写入时记录部分复活。
"""

import json
from dataclasses import dataclass, field


@dataclass(frozen=True, order=True)
class Meta:
    """单次写入的元数据。ordering 即决胜规则前三级：version > timestamp > source。"""

    version: int
    timestamp: int = 0
    source: str = ""  # 来源标识缺失时为空串，保证全序确定


def _value_key(value):
    """值的规范序列化，作为兜底决胜键（全序、与进程/机器无关）。"""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


@dataclass
class FieldEntry:
    meta: Meta
    value: object

    def sort_key(self):
        return (self.meta, _value_key(self.value))


@dataclass
class Record:
    # 记录级最后一次写入（含删除墓碑）
    record_meta: Meta | None = None
    record_deleted: bool = False
    record_values: dict = field(default_factory=dict)
    # 字段级覆盖：fname -> FieldEntry
    fields: dict = field(default_factory=dict)

    def record_key(self):
        """记录级决胜键：Meta 全同时删除（墓碑）优先，再比值。"""
        if self.record_meta is None:
            return None
        return (
            self.record_meta,
            1 if self.record_deleted else 0,
            _value_key(self.record_values),
        )


class Store:
    """单端的存储副本；merge() 可与任意其他副本合并且收敛。"""

    def __init__(self, node_id):
        self.node_id = node_id
        self.version_vector = {}
        self.records = {}

    # ---- 本地写入 ----

    def _tick(self):
        # Lamport：max(本端已知所有分量) + 1，保证因果后者必胜
        version = max(self.version_vector.values(), default=0) + 1
        self.version_vector[self.node_id] = version
        return version

    def _meta(self, timestamp=0, source=None):
        # source 缺省为本端 id；显式传 "" 表示来源缺失
        return Meta(self._tick(), timestamp, self.node_id if source is None else source)

    def set_field(self, key, fname, value, timestamp=0, source=None):
        rec = self.records.setdefault(key, Record())
        rec.fields[fname] = FieldEntry(self._meta(timestamp, source), value)

    def set_record(self, key, values, timestamp=0, source=None):
        rec = self.records.setdefault(key, Record())
        rec.record_meta = self._meta(timestamp, source)
        rec.record_deleted = False
        rec.record_values = dict(values)

    def delete(self, key, timestamp=0, source=None):
        rec = self.records.setdefault(key, Record())
        rec.record_meta = self._meta(timestamp, source)
        rec.record_deleted = True
        rec.record_values = {}

    # ---- 合并 ----

    def merge(self, other):
        """把 other 的状态合并进本端。满足交换/结合/幂等。"""
        for key, remote in other.records.items():
            self._merge_record(key, remote)
        for node, version in other.version_vector.items():
            self.version_vector[node] = max(self.version_vector.get(node, 0), version)

    def _merge_record(self, key, remote):
        local = self.records.setdefault(key, Record())
        rkey, lkey = remote.record_key(), local.record_key()
        if rkey is not None and (lkey is None or rkey > lkey):
            local.record_meta = remote.record_meta
            local.record_deleted = remote.record_deleted
            local.record_values = dict(remote.record_values)
        for fname, entry in remote.fields.items():
            cur = local.fields.get(fname)
            if cur is None or entry.sort_key() > cur.sort_key():
                local.fields[fname] = FieldEntry(entry.meta, entry.value)

    # ---- 读取 / 快照 ----

    def get(self, key):
        """物化记录视图；记录被墓碑支配时返回 None。"""
        rec = self.records.get(key)
        if rec is None:
            return None
        rmeta = rec.record_meta
        out = {}
        if rmeta is not None and not rec.record_deleted:
            out.update(rec.record_values)
        for fname, entry in rec.fields.items():
            if rmeta is None or entry.meta > rmeta:
                out[fname] = entry.value
        if (
            rmeta is not None
            and rec.record_deleted
            and all(rmeta >= entry.meta for entry in rec.fields.values())
        ):
            return None
        return out

    def snapshot(self):
        """规范化快照：{key: 物化值} + 版本向量，用于跨端一致性断言。"""
        return {
            "data": {k: self.get(k) for k in sorted(self.records)},
            "version_vector": dict(sorted(self.version_vector.items())),
        }
