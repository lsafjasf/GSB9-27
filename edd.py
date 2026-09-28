"""单机调度：最小化最大超期时间 (1||Lmax)。

规则：EDD（Earliest Due Date，最早截止期优先）。
把所有任务按截止期 d_j 升序排列（并列时按任务 id 稳定排序），
从时刻 0 开始不间断地依次加工，即可使最大超期 Lmax 最小。

最优性（交换论证）见 README.md。
"""

from dataclasses import dataclass
from typing import List, Sequence, Tuple


@dataclass(frozen=True)
class Job:
    """一个任务：id 唯一标识，p 为加工时间(>0)，d 为截止期。"""

    id: int
    p: int
    d: int

    def __post_init__(self):
        if self.p <= 0:
            raise ValueError(f"加工时间必须为正: job {self.id} p={self.p}")


@dataclass(frozen=True)
class Placement:
    """排产结果中的一条记录。"""

    job: Job
    start: int        # 开始时刻
    completion: int   # 完成时刻 C_j
    lateness: int     # 超期 L_j = C_j - d_j（负值表示提前）


def edd_order(jobs: Sequence[Job]) -> List[Job]:
    """返回 EDD 顺序：按 (截止期, id) 升序。"""
    return sorted(jobs, key=lambda j: (j.d, j.id))


def schedule(jobs: Sequence[Job]) -> List[Placement]:
    """按 EDD 规则排产，从时刻 0 开始无空闲依次加工。"""
    placements: List[Placement] = []
    t = 0
    for job in edd_order(jobs):
        start = t
        t += job.p
        placements.append(
            Placement(job=job, start=start, completion=t, lateness=t - job.d)
        )
    return placements


def max_lateness(placements: Sequence[Placement]) -> int:
    """最大超期 Lmax = max_j (C_j - d_j)。"""
    if not placements:
        return 0
    return max(pl.lateness for pl in placements)


def lower_bound(jobs: Sequence[Job]) -> Tuple[int, str]:
    """Lmax 的下界（对任意排产顺序都成立）。

    依据：对任意任务子集 S，在任意排产中，S 中最后完成的任务
    完成时刻 >= sum_{j in S} p_j（这些加工时间都必须花掉），
    而它的截止期 <= max_{j in S} d_j，故
        Lmax >= sum_{j in S} p_j - max_{j in S} d_j。
    取两类代表性子集：
      - 单任务子集 {j}：Lmax >= p_j - d_j 对每个 j 成立；
      - 全体任务：      Lmax >= sum(p) - max(d)。
    两者取 max 即为一个可快速计算的下界。
    """
    if not jobs:
        return 0, "空任务集，Lmax = 0"
    lb_single = max(j.p - j.d for j in jobs)
    lb_total = sum(j.p for j in jobs) - max(j.d for j in jobs)
    lb = max(lb_single, lb_total)
    reason = (
        f"单任务下界 max(p_j - d_j) = {lb_single}；"
        f"全体下界 sum(p) - max(d) = {lb_total}；取 max 得 {lb}"
    )
    return lb, reason


def report(jobs: Sequence[Job]) -> str:
    """生成人类可读的排产报告。"""
    placements = schedule(jobs)
    lmax = max_lateness(placements)
    lb, reason = lower_bound(jobs)

    lines = []
    lines.append(f"{'任务':>6} {'加工p':>8} {'截止d':>8} {'开始':>8} {'完成C':>8} {'超期L':>8}")
    for pl in placements:
        j = pl.job
        lines.append(
            f"{j.id:>6} {j.p:>8} {j.d:>8} {pl.start:>8} {pl.completion:>8} {pl.lateness:>8}"
        )
    lines.append(f"最大超期 Lmax = {lmax}")
    lines.append(f"下界 LB = {lb}（{reason}）")
    if lmax == lb:
        lines.append("Lmax 已达到下界，该解显然最优。")
    else:
        lines.append("Lmax 与下界的差由交换论证保证仍是最优（下界本身不一定可达）。")
    if lb > 0:
        lines.append(
            f"注意：下界 LB = {lb} > 0，说明无论怎么排序，"
            f"最大超期至少为 {lb}，不可能全部按期完成。"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    demo = [Job(1, 3, 5), Job(2, 1, 2), Job(3, 4, 9), Job(4, 2, 4)]
    print(report(demo))
