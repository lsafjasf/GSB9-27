"""同一份确定性工作负载下，对比「按版本失效」与「固定 TTL」两种缓存方案。

工作负载模型：
- 3 份底层数据（orders/users/products），4 张报表，依赖关系有重叠。
- 读请求按加权随机到达（report_a 为热点键）；数据按随机间隔变更。
- 时间由假时钟注入，结果完全可复现。

运行：python3 benchmark.py
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from report_cache import DataRegistry, ReportCache, TTLReportCache


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


DATA_KEYS = ("orders", "users", "products")
REPORTS = {
    "report_a": ("orders",),
    "report_b": ("orders", "users"),
    "report_c": ("users",),
    "report_d": ("products", "orders"),
}
READ_WEIGHTS = {"report_a": 50, "report_b": 20, "report_c": 20, "report_d": 10}
N_READS = 5000
DATA_CHANGE_PROB = 0.05  # 每次滴答发生一次数据变更的概率
TTL_SECONDS = 10.0


def make_compute(report_name):
    def compute(registry):
        # 报表内容直接由依赖数据的当前值决定，便于校验新鲜度
        return {k: registry.get(k) for k in REPORTS[report_name]}
    return compute


def run_workload(make_cache):
    clock = FakeClock()
    registry = DataRegistry(time_fn=clock)
    for key in DATA_KEYS:
        registry.set(key, 0)
    cache = make_cache(registry, clock)
    for name, deps in REPORTS.items():
        cache.register(name, deps, make_compute(name))

    rng = random.Random(42)
    names = list(READ_WEIGHTS)
    weights = list(READ_WEIGHTS.values())
    stale_results = 0  # 读到的报表与当前底层数据不一致的次数
    data_version = [0]

    for _ in range(N_READS):
        clock.advance(rng.uniform(0.05, 0.5))
        if rng.random() < DATA_CHANGE_PROB:
            data_version[0] += 1
            registry.set(rng.choice(DATA_KEYS), data_version[0])
        report = rng.choices(names, weights=weights)[0]
        result = cache.get(report)
        expected = {k: registry.get(k) for k in REPORTS[report]}
        if result != expected:
            stale_results += 1

    snap = cache.metrics.snapshot()
    snap["stale_results"] = stale_results
    return snap


def main():
    versioned = run_workload(
        lambda registry, clock: ReportCache(registry, time_fn=clock)
    )
    ttl = run_workload(
        lambda registry, clock: TTLReportCache(registry, TTL_SECONDS, time_fn=clock)
    )

    rows = [
        ("命中率", "hit_rate", lambda v: f"{v:.1%}"),
        ("重算次数", "recomputes", str),
        ("读到陈旧数据的次数", "stale_results", str),
        ("平均陈旧时长(秒)", "avg_stale_serve", lambda v: f"{v:.2f}"),
        ("平均失效刷新滞后(秒)", "avg_refresh_lag", lambda v: f"{v:.2f}"),
    ]
    print(f"工作负载: {N_READS} 次读取, 4 张报表, 3 份数据, "
          f"数据变更概率 {DATA_CHANGE_PROB}/滴答, TTL={TTL_SECONDS}s")
    print()
    header = f"{'指标':<22}{'按版本失效':>14}{'固定TTL':>14}"
    print(header)
    print("-" * len(header))
    for label, key, fmt in rows:
        print(f"{label:<22}{fmt(versioned[key]):>14}{fmt(ttl[key]):>14}")


if __name__ == "__main__":
    main()
