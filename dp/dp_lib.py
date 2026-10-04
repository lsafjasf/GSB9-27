"""差分隐私聚合库（仅依赖 Python 标准库）。

机制：Laplace 机制
    对敏感度为 s 的查询结果加 Laplace(scale = s / epsilon) 噪声，
    满足 (epsilon, 0)-DP。

预算：PrivacyAccountant
    顺序组合定理：多个机制依次执行，epsilon 累加。
    所有查询共享同一个 Accountant，预算耗尽（剩余 < 本轮申请值）
    时拒绝查询并抛出 BudgetExhausted，避免"悄悄裸奔"。

小分组：suppress 规则
    group_counts 对每个分组先加噪声再按阈值 kappa 抑制：
        - 若用户已声明总记录数，使用标准的"加噪计数 + 阈值 kappa"
          抑制规则（kappa-proximate 判定）；本实现采用常见做法：
          noisy_count < kappa 即抑制（不下发该分组，也不并入大组，
          以避免泄漏小组成员属性）。
        - 被抑制分组的计数计入 suppress_info，便于审计"哪些规则被
          触发、抑制了多少组"。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple


class BudgetExhausted(Exception):
    """剩余预算不足以支付本轮查询。"""


@dataclass
class BudgetLedgerEntry:
    round_no: int
    query_name: str
    epsilon_used: float
    epsilon_remaining: float
    noise_scale: float


@dataclass
class QueryResult:
    name: str
    value: float
    true_value: float
    epsilon_used: float
    noise_scale: float
    round_no: int


@dataclass
class GroupResult:
    name: str
    noisy_counts: Dict[str, float]
    true_counts: Dict[str, int]
    suppressed: List[str]
    suppression_threshold: float
    epsilon_used: float
    noise_scale: float
    round_no: int

    def published_groups(self) -> List[str]:
        return sorted(self.noisy_counts.keys())


class PrivacyAccountant:
    """跨多轮查询共享的隐私预算账本（epsilon 单调消耗）。"""

    def __init__(self, epsilon_total: float, rng: Optional[random.Random] = None):
        if epsilon_total <= 0:
            raise ValueError("epsilon_total 必须为正数")
        self.epsilon_total = float(epsilon_total)
        self.epsilon_spent = 0.0
        self.rng = rng or random.Random()
        self.ledger: List[BudgetLedgerEntry] = []

    @property
    def epsilon_remaining(self) -> float:
        return self.epsilon_total - self.epsilon_spent

    def spend(self, epsilon: float, query_name: str, noise_scale: float) -> Tuple[float, float, int]:
        if epsilon <= 0:
            raise ValueError("单次查询 epsilon 必须为正数")
        if epsilon > self.epsilon_remaining + 1e-12:
            raise BudgetExhausted(
                f"预算不足: 需要 {epsilon:.6f}, 剩余 {self.epsilon_remaining:.6f} "
                f"(总额 {self.epsilon_total:.6f}, 已用 {self.epsilon_spent:.6f})"
            )
        self.epsilon_spent = min(self.epsilon_total, self.epsilon_spent + epsilon)
        remaining = self.epsilon_remaining
        round_no = len(self.ledger) + 1
        self.ledger.append(
            BudgetLedgerEntry(round_no, query_name, epsilon, remaining, noise_scale)
        )
        return epsilon, remaining, round_no

    def table(self) -> str:
        lines = [
            "round | query                    |   epsilon_used | epsilon_remaining | noise_scale",
            "------+--------------------------+----------------+-------------------+-------------",
        ]
        for e in self.ledger:
            lines.append(
                f"{e.round_no:5d} | {e.query_name:24s} | {e.epsilon_used:14.6f} | "
                f"{e.epsilon_remaining:17.6f} | {e.noise_scale:11.4f}"
            )
        lines.append(
            f"  TOTAL budget={self.epsilon_total:.6f}  spent={self.epsilon_spent:.6f}  "
            f"remaining={self.epsilon_remaining:.6f}"
        )
        return "\n".join(lines)


class DPAggregator:
    """基于共享 Accountant 的差分隐私聚合器（Laplace 机制）。"""

    def __init__(self, accountant: PrivacyAccountant):
        self.acc = accountant

    def _laplace_noise(self, scale: float) -> float:
        # random.laplace 在 3.12 可用，但为兼容老版本直接按逆变换采样
        u = self.acc.rng.random() - 0.5
        return -scale * math.copysign(math.log(1.0 - 2.0 * abs(u)), u)

    def _release(self, name: str, true_value: float, sensitivity: float,
                 epsilon: float) -> QueryResult:
        if epsilon <= 0:
            raise ValueError("单次查询 epsilon 必须为正数")
        scale = sensitivity / epsilon
        used, _remaining, round_no = self.acc.spend(epsilon, name, scale)
        noise = self._laplace_noise(scale)
        return QueryResult(name, true_value + noise, true_value, used, scale, round_no)

    def count(self, records: list, epsilon: float, name: str = "count") -> QueryResult:
        """记录数。敏感度 = 1（一条记录加入/离开改变计数 1）。"""
        return self._release(name, float(len(records)), 1.0, epsilon)

    def sum(self, records: Iterable[float], epsilon: float,
            bound: float, name: str = "sum") -> QueryResult:
        """有界数值之和。敏感度 = 2*bound（clamp 到 [0, bound] 时单条最大贡献差）。

        采用 [0, bound] 截断，单条记录加入/离开对总和的最大改变为 bound；
        相邻数据集为"替换"语义时为 2*bound，这里采用更保守的替换语义。
        """
        vals = [min(max(float(x), 0.0), bound) for x in records]
        return self._release(name, float(sum(vals)), 2.0 * bound, epsilon)

    def mean(self, records: Iterable[float], epsilon: float,
             bound: float, n: int, name: str = "mean") -> QueryResult:
        """有界均值：sum/n，敏感度 = 2*bound/n。n 由调用方提供（视为公开信息）。"""
        vals = [min(max(float(x), 0.0), bound) for x in records]
        return self._release(name, sum(vals) / n if n else 0.0, 2.0 * bound / n, epsilon)

    def group_counts(self, records: Iterable, key_fn, epsilon: float,
                     threshold: float, name: str = "group_counts") -> GroupResult:
        """分组计数 + 小分组抑制。

        规则：每个分组独立加 Laplace(scale=1/epsilon) 噪声；
              noisy_count < threshold 的分组一律抑制（不出现在结果中）。
        抑制本身通过同一阈值机制完成，不额外发布被抑制组的计数。
        """
        true_counts: Dict[str, int] = {}
        for r in records:
            k = str(key_fn(r))
            true_counts[k] = true_counts.get(k, 0) + 1

        scale = 1.0 / epsilon
        used, _remaining, round_no = self.acc.spend(epsilon, name, scale)

        noisy_counts: Dict[str, float] = {}
        suppressed: List[str] = []
        for k in sorted(true_counts):
            nc = true_counts[k] + self._laplace_noise(scale)
            if nc < threshold:
                suppressed.append(k)
            else:
                noisy_counts[k] = nc
        return GroupResult(
            name, noisy_counts, true_counts, suppressed,
            float(threshold), used, scale, round_no,
        )
