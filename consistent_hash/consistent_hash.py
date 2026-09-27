"""一致性哈希环（虚拟节点 + 加权节点），仅依赖标准库。

设计要点：
- 每个节点按权重在环上放置 vnodes * weight 个虚拟节点；
- 环 = 排序后的 (hash_point, node) 列表，键的归属只由环决定，
  与节点注册顺序无关（哈希输入为节点名与虚拟节点序号，确定性）；
- 增删节点只影响被增删节点（在环上顺时针方向）接管的键。
"""

import bisect
import hashlib


def _hash_int(data: bytes) -> int:
    """64 位哈希，跨进程/跨次运行确定。"""
    return int.from_bytes(hashlib.md5(data).digest()[:8], "big")


class HashRing:
    def __init__(self, vnodes: int = 160):
        if vnodes < 1:
            raise ValueError("vnodes must be >= 1")
        self._vnodes = vnodes
        self._weights = {}          # node -> weight
        self._points = []           # 排序后的哈希点
        self._owners = []           # 与 _points 对齐的节点名

    # ---- 节点管理 ----
    def add_node(self, node: str, weight: float = 1.0) -> None:
        if node in self._weights:
            raise ValueError(f"node already exists: {node!r}")
        if weight < 0:
            raise ValueError("weight must be >= 0")
        self._weights[node] = weight
        self._rebuild()

    def remove_node(self, node: str) -> None:
        if node not in self._weights:
            raise KeyError(f"unknown node: {node!r}")
        del self._weights[node]
        self._rebuild()

    @property
    def nodes(self):
        return sorted(self._weights)

    @property
    def ring_size(self) -> int:
        return len(self._points)

    def _rebuild(self) -> None:
        entries = []
        for node, weight in self._weights.items():
            count = int(self._vnodes * weight)
            for i in range(count):
                point = _hash_int(f"{node}#{i}".encode("utf-8"))
                entries.append((point, node))
        entries.sort(key=lambda e: (e[0], e[1]))  # 排序键含节点名，顺序确定
        self._points = [p for p, _ in entries]
        self._owners = [n for _, n in entries]

    # ---- 查询 ----
    def get_node(self, key: str) -> str:
        """返回键所属节点；环为空时抛 RuntimeError。"""
        if not self._points:
            raise RuntimeError("hash ring is empty (no live nodes)")
        point = _hash_int(key.encode("utf-8"))
        idx = bisect.bisect_left(self._points, point)
        if idx == len(self._points):
            idx = 0  # 环绕
        return self._owners[idx]

    def map_keys(self, keys) -> dict:
        """批量映射，返回 {key: node}。"""
        return {k: self.get_node(k) for k in keys}
