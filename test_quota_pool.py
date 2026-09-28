"""Self-tests for quota_pool: invariants + required scenarios."""

import itertools
import random
import unittest

from quota_pool import QuotaPool, QuotaError, max_min_fair


class TestMaxMinFair(unittest.TestCase):
    def test_equal_split(self):
        self.assertEqual(max_min_fair(10, {"a": 10, "b": 10}), {"a": 5, "b": 5})

    def test_caps_redistribute(self):
        self.assertEqual(max_min_fair(10, {"a": 3, "b": 10}), {"a": 3, "b": 7})

    def test_remainder_tiebreak_deterministic(self):
        self.assertEqual(max_min_fair(3, {"a": 5, "b": 5}), {"a": 2, "b": 1})
        self.assertEqual(max_min_fair(3, {"b": 5, "a": 5}), {"a": 2, "b": 1})

    def test_never_exceeds_and_work_conserving(self):
        for seed in range(100):
            rng = random.Random(seed)
            caps = {"t%d" % i: rng.randint(0, 20)
                    for i in range(rng.randint(1, 6))}
            capacity = rng.randint(0, 60)
            alloc = max_min_fair(capacity, caps)
            for tid, got in alloc.items():
                self.assertLessEqual(got, caps[tid])
            self.assertLessEqual(sum(alloc.values()), capacity)
            if sum(alloc.values()) < capacity:
                self.assertTrue(all(alloc[t] == caps[t] for t in caps))


class TestAdmissionControl(unittest.TestCase):
    def test_guarantees_never_oversubscribe(self):
        pool = QuotaPool(100)
        pool.add_tenant("a", 60, 100)
        pool.add_tenant("b", 40, 100)
        with self.assertRaises(QuotaError):
            pool.add_tenant("c", 1, 100)  # 60+40+1 > 100
        with self.assertRaises(QuotaError):
            pool.add_tenant("d", 50, 40)  # limit < guarantee

    def test_unknown_tenant_rejected(self):
        pool = QuotaPool(100)
        pool.add_tenant("a", 10, 100)
        with self.assertRaises(QuotaError):
            pool.tick({"ghost": 5})


class TestSingleTenant(unittest.TestCase):
    def test_borrows_up_to_limit(self):
        pool = QuotaPool(100)
        pool.add_tenant("solo", 20, 100)
        grants = pool.tick({"solo": 100})
        self.assertEqual(grants["solo"], 100)  # idle pool fully borrowable

    def test_limit_is_hard_ceiling(self):
        pool = QuotaPool(100)
        pool.add_tenant("solo", 20, 60)
        grants = pool.tick({"solo": 1000})
        self.assertEqual(grants["solo"], 60)


class TestAllSaturated(unittest.TestCase):
    def test_guarantees_honored_under_full_contention(self):
        pool = QuotaPool(100)
        for i in range(4):
            pool.add_tenant("t%d" % i, 25, 100)
        grants = pool.tick({"t%d" % i: 100 for i in range(4)})
        for i in range(4):
            self.assertEqual(grants["t%d" % i], 25)
        self.assertEqual(sum(grants.values()), 100)

    def test_slack_split_evenly_and_deterministically(self):
        pool = QuotaPool(100)
        pool.add_tenant("a", 10, 100)
        pool.add_tenant("b", 10, 100)
        pool.add_tenant("c", 10, 100)
        grants = pool.tick({"a": 100, "b": 100, "c": 100})
        # 70 slack over 3 -> 24/23/23, remainder to smallest tid.
        self.assertEqual((grants["a"], grants["b"], grants["c"]), (34, 33, 33))


class TestBorrowAndReclaim(unittest.TestCase):
    def test_reclaim_restores_guarantee_same_tick(self):
        pool = QuotaPool(100, tick_ms=10)
        pool.add_tenant("owner", 50, 100)
        pool.add_tenant("borrower", 30, 100)
        for _ in range(5):  # owner idle; borrower eats the whole pool
            grants = pool.tick({"borrower": 100})
            self.assertEqual(grants["borrower"], 100)
        grants = pool.tick({"owner": 50, "borrower": 100})  # owner returns
        self.assertEqual(grants["owner"], 50)               # guarantee back now
        self.assertEqual(grants["borrower"], 50)            # cut to own 30 + 20 slack
        self.assertEqual(len(pool.reclaim_events), 1)
        event = pool.reclaim_events[0]
        self.assertEqual(event.latency_ticks, 0)            # same-tick reclaim
        self.assertEqual(event.revoked_units, 50)           # borrower's 50 borrowed units revoked
        summary = pool.reclaim_summary()
        self.assertLessEqual(summary["max_latency_ms"], summary["bound_ms"])


class TestDynamicJoin(unittest.TestCase):
    def test_join_rebalances_without_breaking_guarantees(self):
        pool = QuotaPool(100)
        pool.add_tenant("a", 40, 100)
        pool.add_tenant("b", 40, 100)
        grants = pool.tick({"a": 100, "b": 100})
        self.assertEqual((grants["a"], grants["b"]), (50, 50))
        pool.add_tenant("c", 20, 100)  # dynamic join: 40+40+20 = 100, ok
        with self.assertRaises(QuotaError):
            pool.add_tenant("d", 1, 100)  # would oversubscribe
        for _ in range(10):
            grants = pool.tick({"a": 100, "b": 100, "c": 100})
            self.assertEqual((grants["a"], grants["b"], grants["c"]), (40, 40, 20))
        pool.remove_tenant("b")  # capacity freed for the rest
        grants = pool.tick({"a": 100, "c": 100})
        # 40 slack split max-min fairly: 20 each on top of guarantees.
        self.assertEqual((grants["a"], grants["c"]), (60, 40))


class TestOrderIndependence(unittest.TestCase):
    def test_request_order_cannot_change_outcome(self):
        demands = {"a": 100, "b": 100, "c": 37, "d": 5}
        outcomes = set()
        for perm in itertools.permutations(demands):
            pool = QuotaPool(100)
            for tid in ("a", "b", "c", "d"):
                pool.add_tenant(tid, 10, 100)
            ordered = {tid: demands[tid] for tid in perm}
            outcomes.add(tuple(sorted(pool.tick(ordered).items())))
        self.assertEqual(len(outcomes), 1)

    def test_no_long_term_starvation(self):
        # A hog can never squeeze the victim below its guarantee, and the
        # victim's long-run satisfaction is 100% regardless of hog pressure.
        pool = QuotaPool(100)
        pool.add_tenant("hog", 10, 100)
        pool.add_tenant("victim", 30, 100)
        satisfied = 0
        for i in range(200):
            victim_demand = 30 if i % 3 else 0  # sporadic demand
            grants = pool.tick({"hog": 1000, "victim": victim_demand})
            self.assertLessEqual(grants["hog"], 100)
            if victim_demand:
                self.assertGreaterEqual(grants["victim"], 30)
                satisfied += 1
        self.assertEqual(satisfied, sum(1 for i in range(200) if i % 3))


class TestFuzzInvariants(unittest.TestCase):
    def test_invariants_and_determinism_under_random_load(self):
        for seed in range(20):
            rng = random.Random(seed)
            n_tenants = rng.randint(1, 6)
            specs = {}
            budget = 100
            for i in range(n_tenants):
                guarantee = rng.randint(0, budget // max(1, n_tenants - i))
                budget -= guarantee
                specs["t%d" % i] = (guarantee, guarantee + rng.randint(0, 100))
            trace = [{tid: rng.randint(0, 150) for tid in specs}
                     for _ in range(200)]
            runs = []
            for _ in range(2):  # replay identical trace -> identical grants
                pool = QuotaPool(100)
                for tid, (guarantee, limit) in specs.items():
                    pool.add_tenant(tid, guarantee, limit)
                runs.append([pool.tick(demands) for demands in trace])
            self.assertEqual(runs[0], runs[1])
            # INV checks run inside every tick(); spot-check the totals here.
            for grants in runs[0]:
                self.assertLessEqual(sum(grants.values()), 100)
                for tid, grant in grants.items():
                    self.assertLessEqual(grant, specs[tid][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
