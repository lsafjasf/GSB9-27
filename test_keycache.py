"""Self-tests for keycache: normal polling, fetch failure, rollback,
and the exactly-expired boundary. Standard library only."""

import unittest

from keycache import KeyCache, KeyMaterial, NoUsableKeyError


class FakeClock:
    """Injectable monotonic clock."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


class ScriptedFetcher:
    """Returns queued keys or raises queued exceptions."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def __call__(self) -> KeyMaterial:
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class TestNormalPolling(unittest.TestCase):
    def test_cache_hit_until_expiry_then_refetch(self) -> None:
        clock = FakeClock(0.0)
        fetcher = ScriptedFetcher(
            [
                KeyMaterial("key-v1", 1, expires_at=100.0),
                KeyMaterial("key-v2", 2, expires_at=200.0),
            ]
        )
        cache = KeyCache(fetcher, now=clock)

        # Initial fetch at t=0.
        self.assertEqual(cache.get_key().version, 1)
        # Cache hit: no refetch before expiry.
        clock.t = 99.0
        self.assertEqual(cache.get_key().version, 1)
        self.assertEqual(fetcher.calls, 1)

        # Expiry triggers the second fetch.
        clock.t = 100.0
        self.assertEqual(cache.get_key().version, 2)
        self.assertEqual(fetcher.calls, 2)

        # Subsequent calls are served from cache again.
        clock.t = 150.0
        self.assertEqual(cache.get_key().version, 2)
        self.assertEqual(fetcher.calls, 2)

        self.assertEqual(cache.version_history, [1, 2])

    def test_same_version_refetch_is_accepted(self) -> None:
        clock = FakeClock(0.0)
        fetcher = ScriptedFetcher(
            [
                KeyMaterial("key-v1", 1, expires_at=10.0),
                KeyMaterial("key-v1-rotated", 1, expires_at=20.0),
            ]
        )
        cache = KeyCache(fetcher, now=clock)
        self.assertEqual(cache.get_key().key, "key-v1")
        clock.t = 10.0
        self.assertEqual(cache.get_key().key, "key-v1-rotated")
        self.assertEqual(cache.version_history, [1, 1])
        self.assertEqual(cache.rejections, [])


class TestFetchFailure(unittest.TestCase):
    def test_degraded_service_with_unexpired_key(self) -> None:
        clock = FakeClock(0.0)
        # Cached key valid until t=300; fetches at t=100 and t=250 fail.
        fetcher = ScriptedFetcher(
            [
                KeyMaterial("key-v1", 1, expires_at=300.0),
                ConnectionError("remote 503"),
                ConnectionError("remote 503"),
            ]
        )
        cache = KeyCache(fetcher, now=clock)
        self.assertEqual(cache.get_key().version, 1)

        # Background poller refreshes at t=100; fetch fails but the cached
        # key is valid until t=300 -> degraded service, 200s window left.
        clock.t = 100.0
        served = cache.refresh()
        self.assertEqual(served.version, 1)
        self.assertEqual(served.key, "key-v1")
        self.assertAlmostEqual(cache.degradation_window(), 200.0)

        clock.t = 250.0  # second failed refresh, 50s window left
        self.assertEqual(cache.refresh().version, 1)
        self.assertEqual(cache.get_key().version, 1)
        self.assertAlmostEqual(cache.degradation_window(), 50.0)

        degraded = [e for e in cache.events if e.kind == "degraded"]
        self.assertEqual(len(degraded), 2)
        self.assertTrue(all("remote 503" in e.reason for e in degraded))

    def test_failure_after_expiry_is_unusable(self) -> None:
        clock = FakeClock(0.0)
        fetcher = ScriptedFetcher(
            [
                KeyMaterial("key-v1", 1, expires_at=100.0),
                ConnectionError("remote down"),
            ]
        )
        cache = KeyCache(fetcher, now=clock)
        self.assertEqual(cache.get_key().version, 1)

        clock.t = 100.0  # exactly expired, no degradation window
        self.assertEqual(cache.degradation_window(), 0.0)
        with self.assertRaises(NoUsableKeyError):
            cache.get_key()
        self.assertEqual(
            [e.kind for e in cache.events], ["accepted", "expired"]
        )

    def test_recovery_after_degraded_period(self) -> None:
        clock = FakeClock(0.0)
        fetcher = ScriptedFetcher(
            [
                KeyMaterial("key-v1", 1, expires_at=300.0),
                ConnectionError("remote 503"),
                KeyMaterial("key-v2", 2, expires_at=400.0),
            ]
        )
        cache = KeyCache(fetcher, now=clock)
        self.assertEqual(cache.get_key().version, 1)

        clock.t = 100.0  # refresh fails -> degraded, still serving v1
        self.assertEqual(cache.refresh().version, 1)
        self.assertEqual(cache.get_key().version, 1)
        clock.t = 200.0  # refresh succeeds -> recovered to v2
        self.assertEqual(cache.refresh().version, 2)
        self.assertEqual(cache.get_key().version, 2)  # recovered
        self.assertEqual(cache.version_history, [1, 2])


class TestVersionRollback(unittest.TestCase):
    def test_rollback_rejected_old_key_kept_while_valid(self) -> None:
        clock = FakeClock(0.0)
        fetcher = ScriptedFetcher(
            [
                KeyMaterial("key-v2", 2, expires_at=400.0),
                KeyMaterial("stale-v1", 1, expires_at=400.0),  # rollback
            ]
        )
        cache = KeyCache(fetcher, now=clock)
        self.assertEqual(cache.get_key().version, 2)

        # Background poller proactively refreshes while v2 is still valid;
        # the source now serves a rolled-back v1.
        clock.t = 300.0
        served = cache.refresh()
        self.assertEqual(served.version, 2)  # rolled-back v1 never served
        self.assertEqual(cache.get_key().version, 2)

        self.assertEqual(len(cache.rejections), 1)
        rec = cache.rejections[0]
        self.assertEqual(rec.version, 1)
        self.assertIn("rollback", rec.reason)
        self.assertEqual(cache.version_history, [2])  # v1 never accepted

    def test_rollback_after_expiry_is_unusable(self) -> None:
        clock = FakeClock(0.0)
        fetcher = ScriptedFetcher(
            [
                KeyMaterial("key-v2", 2, expires_at=100.0),
                KeyMaterial("stale-v1", 1, expires_at=200.0),  # rollback
            ]
        )
        cache = KeyCache(fetcher, now=clock)
        self.assertEqual(cache.get_key().version, 2)

        clock.t = 100.0
        with self.assertRaises(NoUsableKeyError):
            cache.get_key()
        self.assertEqual(len(cache.rejections), 1)
        self.assertEqual(cache.cached.version, 2)  # old entry retained, not replaced


class TestExactlyExpiredBoundary(unittest.TestCase):
    def test_one_second_before_expiry_is_cache_hit(self) -> None:
        clock = FakeClock(0.0)
        fetcher = ScriptedFetcher(
            [KeyMaterial("key-v1", 1, expires_at=100.0)]
        )
        cache = KeyCache(fetcher, now=clock)
        cache.get_key()

        clock.t = 99.0
        self.assertFalse(cache.cached.is_expired(clock()))
        cache.get_key()
        self.assertEqual(fetcher.calls, 1)  # no refetch

    def test_exactly_at_expiry_triggers_refetch(self) -> None:
        clock = FakeClock(0.0)
        fetcher = ScriptedFetcher(
            [
                KeyMaterial("key-v1", 1, expires_at=100.0),
                KeyMaterial("key-v2", 2, expires_at=200.0),
            ]
        )
        cache = KeyCache(fetcher, now=clock)
        cache.get_key()

        clock.t = 100.0  # now == expires_at: expired, refetch
        self.assertTrue(cache.cached.is_expired(clock()))
        self.assertEqual(cache.get_key().version, 2)
        self.assertEqual(fetcher.calls, 2)

    def test_exactly_at_expiry_with_failing_fetch_raises(self) -> None:
        clock = FakeClock(0.0)
        fetcher = ScriptedFetcher(
            [
                KeyMaterial("key-v1", 1, expires_at=100.0),
                ConnectionError("boom"),
            ]
        )
        cache = KeyCache(fetcher, now=clock)
        cache.get_key()
        clock.t = 100.0
        with self.assertRaises(NoUsableKeyError):
            cache.get_key()


if __name__ == "__main__":
    unittest.main(verbosity=2)
