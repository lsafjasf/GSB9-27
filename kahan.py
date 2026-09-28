"""补偿求和（Kahan–Babuška–Neumaier）累加器，仅依赖标准库。

补偿项更新公式（Neumaier 变体，对“大数吃小数”更稳）：

    t = total + x
    if |total| >= |x|:
        comp += (total - t) + x      # x 被吃掉的部分进补偿
    else:
        comp += (x - t) + total      # total 被吃掉的部分进补偿
    total = t

当前和取值：total + comp。
误差界：朴素累加为 O(n·eps)，本算法为 O(eps + n·eps²)。
"""

__all__ = ["KahanSum"]


class KahanSum:
    __slots__ = ("total", "comp", "count")

    def __init__(self, values=()):
        self.total = 0.0   # 主累加量
        self.comp = 0.0    # 补偿项（丢失的低位）
        self.count = 0     # 已累加元素个数
        if values:
            self.add_many(values)

    def add(self, x):
        """增量累加单个值（Neumaier 更新）。"""
        t = self.total + x
        if abs(self.total) >= abs(x):
            self.comp += (self.total - t) + x
        else:
            self.comp += (x - t) + self.total
        self.total = t
        self.count += 1
        return self

    def add_many(self, values):
        """分批喂入一批值。"""
        for x in values:
            self.add(x)
        return self

    @property
    def value(self):
        """当前和：主量 + 补偿项。"""
        return self.total + self.comp

    def reset(self):
        """重置为初始状态。"""
        self.total = 0.0
        self.comp = 0.0
        self.count = 0
        return self

    def merge(self, other):
        """合并另一个累加器（视为其元素在本累加器之后喂入），原地合并并返回 self。

        合并公式（对两个 (total, comp) 再做一次 Neumaier 补偿）：

            t = a.total + b.total
            交叉修正项按 |a.total| 与 |b.total| 的大小关系选取，
            comp = a.comp + b.comp + 交叉修正项
        """
        t = self.total + other.total
        if abs(self.total) >= abs(other.total):
            cross = (self.total - t) + other.total
        else:
            cross = (other.total - t) + self.total
        self.comp = self.comp + other.comp + cross
        self.total = t
        self.count += other.count
        return self

    def merged(self, other):
        """返回合并后的新累加器，不修改双方。"""
        out = KahanSum()
        out.total, out.comp, out.count = self.total, self.comp, self.count
        return out.merge(other)

    def __repr__(self):
        return f"KahanSum(value={self.value!r}, count={self.count})"
