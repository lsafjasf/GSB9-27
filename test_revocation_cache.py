"""revocation_cache 自测：正常命中 / 缓存过期 / 源不可用 / 刚被吊销 / 边界。"""

import unittest

from revocation_cache import (
    Decision,
    FailPolicy,
    RevocationCache,
    RevocationSourceError,
    Status,
)


class FakeClock:
    def __init__(self, start: float = 1_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSource:
    """可控吊销源：可改状态、可模拟宕机，并记录查询次数。"""

    def __init__(self):
        self.statuses = {}
        self.down = False
        self.calls = []

    def revoke(self, serial):
        self.statuses[serial] = Status.REVOKED

    def set_down(self, down: bool):
        self.down = down

    def __call__(self, serial):
        self.calls.append(serial)
        if self.down:
            raise RevocationSourceError("source unavailable")
        return self.statuses.get(serial, Status.GOOD)


def make_cache(source, clock, ttl=60.0, max_entries=100,
               policy=FailPolicy.FAIL_CLOSED):
    return RevocationCache(
        fetcher=source, ttl=ttl, max_entries=max_entries,
        policy=policy, time_fn=clock,
    )


class TestNormalHit(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.source = FakeSource()
        self.cache = make_cache(self.source, self.clock)

    def test_second_check_hits_cache(self):
        r1 = self.cache.check("A")
        r2 = self.cache.check("A")
        self.assertEqual(r1.decision, Decision.ALLOW)
        self.assertFalse(r1.from_cache)
        self.assertEqual(r2.decision, Decision.ALLOW)
        self.assertTrue(r2.from_cache)
        self.assertEqual(r2.reason, "cache_hit")
        self.assertEqual(len(self.source.calls), 1)  # 只回源一次
        self.assertEqual(self.cache.stats.hits, 1)
        self.assertEqual(self.cache.stats.misses, 1)
        self.assertAlmostEqual(self.cache.stats.hit_rate, 0.5)

    def test_revoked_status_is_cached_and_denied(self):
        self.source.revoke("B")
        r1 = self.cache.check("B")
        r2 = self.cache.check("B")
        self.assertEqual(r1.decision, Decision.DENY)
        self.assertEqual(r2.decision, Decision.DENY)
        self.assertTrue(r2.from_cache)
        self.assertEqual(len(self.source.calls), 1)


class TestExpiry(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.source = FakeSource()
        self.cache = make_cache(self.source, self.clock, ttl=60.0)

    def test_expired_entry_triggers_refetch(self):
        self.cache.check("A")
        self.clock.advance(61.0)
        r = self.cache.check("A")
        self.assertFalse(r.from_cache)
        self.assertEqual(r.reason, "fetch_ok")
        self.assertEqual(len(self.source.calls), 2)
        self.assertEqual(self.cache.stats.expirations, 1)

    def test_ttl_boundary_is_expired(self):
        # 边界：恰好到达 expires_at 视为过期（now < expires_at 才算有效）
        self.cache.check("A")
        self.clock.advance(60.0)
        r = self.cache.check("A")
        self.assertFalse(r.from_cache)
        self.assertEqual(self.cache.stats.expirations, 1)

    def test_just_before_boundary_still_hits(self):
        self.cache.check("A")
        self.clock.advance(59.999)
        r = self.cache.check("A")
        self.assertTrue(r.from_cache)
        self.assertAlmostEqual(r.age, 59.999)


class TestSourceDown(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.source = FakeSource()

    def test_fail_closed_denies_without_entry(self):
        cache = make_cache(self.source, self.clock, policy=FailPolicy.FAIL_CLOSED)
        self.source.set_down(True)
        r = cache.check("A")
        self.assertEqual(r.decision, Decision.DENY)
        self.assertEqual(r.reason, "source_down_fail_closed")
        self.assertIsNone(r.status)
        self.assertEqual(cache.stats.fetch_errors, 1)

    def test_fail_closed_denies_even_with_stale_entry(self):
        cache = make_cache(self.source, self.clock, policy=FailPolicy.FAIL_CLOSED)
        self.cache_good_then_expire(cache)
        r = cache.check("A")
        self.assertEqual(r.decision, Decision.DENY)
        self.assertTrue(r.stale)  # 明确告知存在过期数据但仍拒绝

    def test_fail_open_serves_stale_entry(self):
        cache = make_cache(self.source, self.clock, policy=FailPolicy.FAIL_OPEN)
        self.cache_good_then_expire(cache)
        r = cache.check("A")
        self.assertEqual(r.decision, Decision.ALLOW)
        self.assertTrue(r.stale)
        self.assertTrue(r.from_cache)
        self.assertEqual(r.reason, "source_down_stale_fail_open")
        self.assertEqual(cache.stats.stale_serves, 1)

    def test_fail_open_allows_without_any_entry(self):
        cache = make_cache(self.source, self.clock, policy=FailPolicy.FAIL_OPEN)
        self.source.set_down(True)
        r = cache.check("A")
        self.assertEqual(r.decision, Decision.ALLOW)
        self.assertEqual(r.reason, "source_down_fail_open")
        self.assertFalse(r.from_cache)

    def test_source_recovers_after_outage(self):
        cache = make_cache(self.source, self.clock, policy=FailPolicy.FAIL_CLOSED)
        self.source.set_down(True)
        self.assertEqual(cache.check("A").decision, Decision.DENY)
        self.source.set_down(False)
        r = cache.check("A")
        self.assertEqual(r.decision, Decision.ALLOW)
        self.assertEqual(r.reason, "fetch_ok")

    def cache_good_then_expire(self, cache):
        cache.check("A")               # 缓存 GOOD
        self.clock.advance(61.0)       # 过期
        self.source.set_down(True)     # 源宕机


class TestFreshRevocation(unittest.TestCase):
    """刚被吊销的证书不得因缓存继续放行。"""

    def setUp(self):
        self.clock = FakeClock()
        self.source = FakeSource()
        self.cache = make_cache(self.source, self.clock, ttl=60.0)

    def test_invalidate_forces_refetch_and_denies(self):
        self.cache.check("A")          # 缓存 GOOD
        self.source.revoke("A")        # 源端刚吊销
        removed = self.cache.invalidate("A")   # 吊销事件推送 -> 主动失效
        self.assertTrue(removed)
        r = self.cache.check("A")
        self.assertEqual(r.decision, Decision.DENY)
        self.assertFalse(r.from_cache)
        self.assertEqual(r.status, Status.REVOKED)
        self.assertEqual(self.cache.stats.invalidations, 1)

    def test_invalidate_missing_serial_returns_false(self):
        self.assertFalse(self.cache.invalidate("NOPE"))
        self.assertEqual(self.cache.stats.invalidations, 1)

    def test_stale_window_bounded_by_ttl_without_invalidation(self):
        # 无主动失效时的最坏不一致窗口 == TTL，到期后必然与源一致
        self.cache.check("A")
        self.source.revoke("A")
        self.clock.advance(59.0)
        self.assertEqual(self.cache.check("A").decision, Decision.ALLOW)  # 窗口内
        self.clock.advance(2.0)  # 总计 61s > TTL
        r = self.cache.check("A")
        self.assertEqual(r.decision, Decision.DENY)  # 窗口外已与源一致
        self.assertEqual(self.cache.max_staleness, 60.0)


class TestCapacity(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.source = FakeSource()

    def test_lru_eviction_at_capacity(self):
        cache = make_cache(self.source, self.clock, max_entries=2)
        cache.check("A")
        cache.check("B")
        cache.check("A")          # A 变最近使用，B 最久未用
        cache.check("C")          # 触发淘汰 B
        self.assertEqual(len(cache), 2)
        self.assertEqual(cache.stats.evictions, 1)
        self.assertIsNone(cache.cached_entry_info("B"))
        self.assertIsNotNone(cache.cached_entry_info("A"))

    def test_capacity_one(self):
        cache = make_cache(self.source, self.clock, max_entries=1)
        cache.check("A")
        cache.check("B")
        self.assertEqual(len(cache), 1)
        self.assertEqual(cache.stats.evictions, 1)

    def test_invalid_config_rejected(self):
        with self.assertRaises(ValueError):
            make_cache(self.source, self.clock, ttl=0)
        with self.assertRaises(ValueError):
            make_cache(self.source, self.clock, max_entries=0)


class TestObservability(unittest.TestCase):
    def test_cached_entry_info_reports_expiry(self):
        clock = FakeClock()
        source = FakeSource()
        cache = make_cache(source, clock, ttl=60.0)
        cache.check("A")
        info = cache.cached_entry_info("A")
        self.assertEqual(info["status"], "good")
        self.assertFalse(info["expired"])
        clock.advance(61.0)
        info = cache.cached_entry_info("A")
        self.assertTrue(info["expired"])
        self.assertAlmostEqual(info["age"], 61.0)
        self.assertIsNone(cache.cached_entry_info("MISSING"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
