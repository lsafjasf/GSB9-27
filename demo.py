"""场景化演示：输出命中率数据、两种失败策略结果、缓存一致性数据。

运行：python3 demo.py
"""

import random

from revocation_cache import (
    Decision,
    FailPolicy,
    RevocationCache,
    RevocationSourceError,
    Status,
)


class SimClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, s):
        self.now += s


class SimSource:
    """模拟吊销源：1ms 查询延迟，可宕机，可吊销。"""

    def __init__(self, clock):
        self.clock = clock
        self.revoked = set()
        self.down = False
        self.queries = 0

    def __call__(self, serial):
        self.queries += 1
        self.clock.advance(0.001)  # 每次回源耗时 1ms
        if self.down:
            raise RevocationSourceError("connection timeout")
        return Status.REVOKED if serial in self.revoked else Status.GOOD


def fmt_pct(x):
    return f"{x * 100:.2f}%"


def scenario_hit_rate():
    print("=" * 64)
    print("场景 1：正常负载下的命中率（TTL=60s, 容量=150, 证书池=200, 5000 次查询）")
    print("=" * 64)
    clock = SimClock()
    source = SimSource(clock)
    cache = RevocationCache(source, ttl=60.0, max_entries=150,
                            policy=FailPolicy.FAIL_CLOSED, time_fn=clock)
    rng = random.Random(42)
    pool = [f"CERT-{i:04d}" for i in range(200)]
    # 热门证书被更频繁查询（zipf 式权重），模拟真实流量倾斜
    weights = [1.0 / (i + 1) for i in range(len(pool))]
    for _ in range(5000):
        serial = rng.choices(pool, weights=weights, k=1)[0]
        cache.check(serial)
        clock.advance(rng.uniform(0.05, 0.2))  # 查询间隔，驱动 TTL 过期
    s = cache.stats
    print(f"  总查询        : {s.hits + s.misses}")
    print(f"  缓存命中      : {s.hits}")
    print(f"  未命中(回源)  : {s.misses}  (其中因过期重查 {s.expirations} 次)")
    print(f"  命中率        : {fmt_pct(s.hit_rate)}")
    print(f"  LRU 淘汰      : {s.evictions} 次 (容量上界 150, 池 200)")
    print(f"  回源总次数    : {source.queries}  (缓存挡掉了 "
          f"{fmt_pct(1 - source.queries / 5000)} 的回源压力)")


def scenario_fail_policy():
    print()
    print("=" * 64)
    print("场景 2：吊销源宕机 30s，两种策略对比（TTL=60s）")
    print("=" * 64)
    for policy in (FailPolicy.FAIL_CLOSED, FailPolicy.FAIL_OPEN):
        clock = SimClock()
        source = SimSource(clock)
        cache = RevocationCache(source, ttl=60.0, max_entries=100,
                                policy=policy, time_fn=clock)
        certs = [f"CERT-{i:04d}" for i in range(10)]
        for c in certs:  # 先预热缓存
            cache.check(c)
        clock.advance(61.0)          # 全部过期
        source.down = True           # 源宕机
        results = [cache.check(c) for c in certs for _ in range(3)]
        allows = sum(1 for r in results if r.decision is Decision.ALLOW)
        denies = len(results) - allows
        stale_served = cache.stats.stale_serves
        note = (f"使用过期数据放行 {stale_served} 次" if stale_served
                else "存在过期数据但未使用")
        print(f"  策略 {policy.value:<11}: 放行 {allows:>2} / 拒绝 {denies:>2}"
              f"  ({note}, 回源失败 {cache.stats.fetch_errors} 次)")
        print(f"    样例 reason: {results[0].reason}")


def scenario_fresh_revocation():
    print()
    print("=" * 64)
    print("场景 3：证书刚被吊销，缓存与实时查询的一致性（TTL=60s）")
    print("=" * 64)

    # 3a：无主动失效 —— 不一致窗口 = TTL
    clock = SimClock()
    source = SimSource(clock)
    cache = RevocationCache(source, ttl=60.0, max_entries=100,
                            policy=FailPolicy.FAIL_CLOSED, time_fn=clock)
    cache.check("VICTIM")
    source.revoked.add("VICTIM")     # t=0.001 时源端已吊销
    stale_allows = 0
    t = 0.0
    first_deny_at = None
    while t <= 61.0:
        r = cache.check("VICTIM")
        if r.decision is Decision.ALLOW:
            stale_allows += 1
        elif first_deny_at is None:
            first_deny_at = clock.now
        clock.advance(1.0)
        t += 1.0
    print(f"  [无 invalidate] 吊销后仍被缓存放行的查询: {stale_allows} 次")
    print(f"  [无 invalidate] 首次拒绝出现在吊销后约 {first_deny_at:.0f}s"
          f"  (不一致窗口上界 = TTL = {cache.max_staleness:.0f}s)")

    # 3b：有主动失效 —— 立即一致
    clock = SimClock()
    source = SimSource(clock)
    cache = RevocationCache(source, ttl=60.0, max_entries=100,
                            policy=FailPolicy.FAIL_CLOSED, time_fn=clock)
    cache.check("VICTIM")
    source.revoked.add("VICTIM")
    cache.invalidate("VICTIM")       # 吊销事件推送 -> 主动失效
    r = cache.check("VICTIM")
    print(f"  [有 invalidate] 吊销后立即查询: decision={r.decision.value},"
          f" from_cache={r.from_cache}, status={r.status.value}")
    print(f"  [有 invalidate] 不一致窗口: 0s (缓存与实时查询一致)")


if __name__ == "__main__":
    scenario_hit_rate()
    scenario_fail_policy()
    scenario_fresh_revocation()
