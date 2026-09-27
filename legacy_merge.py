"""原始（有缺陷）的多端合并实现 —— 仅用于复现不一致问题，请勿在生产使用。

已知缺陷：
1. 字段元数据只有 wall-clock 时间戳，没有逻辑版本号；时钟回拨会直接颠倒胜负。
2. 时间戳相同时「保留先到者」，结果由更新到达顺序决定 -> 各端不收敛。
3. 来源标识缺失时没有任何决胜手段。
4. 删除即物理移除，没有墓碑；删除与更新竞争的结果依赖到达顺序。
5. 版本向量按「本端视角」覆盖而非逐分量取 max，合并后各端向量不一致。
"""


class LegacyStore:
    def __init__(self, node_id):
        self.node_id = node_id
        # key -> {"fields": {fname: (timestamp, source, value)}, "deleted": bool}
        self.records = {}
        self.version_vector = {}

    def set_field(self, key, fname, value, timestamp=0, source=None):
        rec = self.records.setdefault(key, {"fields": {}, "deleted": False})
        rec["fields"][fname] = (timestamp, source, value)
        self.version_vector[self.node_id] = timestamp

    def set_record(self, key, values, timestamp=0, source=None):
        self.records[key] = {
            "fields": {f: (timestamp, source, v) for f, v in values.items()},
            "deleted": False,
        }
        self.version_vector[self.node_id] = timestamp

    def delete(self, key, timestamp=0, source=None):
        # 缺陷 4：直接物理删除，没有墓碑
        self.records.pop(key, None)
        self.version_vector[self.node_id] = timestamp

    def merge(self, other):
        for key, rrec in other.records.items():
            lrec = self.records.setdefault(key, {"fields": {}, "deleted": False})
            for fname, (ts, src, val) in rrec["fields"].items():
                cur = lrec["fields"].get(fname)
                # 缺陷 2/3：时间戳相同（或来源缺失无法比较）时保留先到者，
                # 最终值取决于更新到达顺序。
                if cur is None or ts > cur[0]:
                    lrec["fields"][fname] = (ts, src, val)
        # 缺陷 5：直接用对端向量覆盖本端已知的同名分量
        for node, ts in other.version_vector.items():
            if ts > self.version_vector.get(node, 0):
                self.version_vector[node] = ts

    def get(self, key):
        rec = self.records.get(key)
        if rec is None:
            return None
        return {f: v for f, (_, _, v) in rec["fields"].items()}
