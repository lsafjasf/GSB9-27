"""Before/after comparison.

Runs identical scenarios against the buggy and fixed stores and prints the
visible behaviour side by side.

Run:  python3 reproduce.py
"""

from clock import FakeClock
from store_buggy import TTLStore as BuggyStore
from store_fixed import TTLStore as FixedStore


def scenario_point_get():
    """after ttl elapses, point lookup must miss"""
    results = {}
    for name, cls in (("buggy", BuggyStore), ("fixed", FixedStore)):
        clock = FakeClock()
        store = cls(clock)
        store.put("k", {"v": 1}, ttl=10)
        clock.advance(10)
        results[name] = store.get("k")
    return results, "k expired at t=10 -> get()"


def scenario_scan():
    """expired rows must not appear in range scans"""
    results = {}
    for name, cls in (("buggy", BuggyStore), ("fixed", FixedStore)):
        clock = FakeClock()
        store = cls(clock)
        store.put("a", {"v": 1}, ttl=10)
        store.put("b", {"v": 2}, ttl=20)
        clock.advance(15)
        results[name] = list(store.scan())
    return results, "a expired, scan() after t=15"


def scenario_batch_get():
    """batch point reads must apply the same check"""
    results = {}
    for name, cls in (("buggy", BuggyStore), ("fixed", FixedStore)):
        clock = FakeClock()
        store = cls(clock)
        store.put("a", {"v": 1}, ttl=10)
        clock.advance(15)
        results[name] = store.get_many(["a", "missing"])
    return results, "get_many([a, missing]) after expiry"


def scenario_aggregate():
    """aggregates must not count/sum expired rows"""
    results = {}
    for name, cls in (("buggy", BuggyStore), ("fixed", FixedStore)):
        clock = FakeClock()
        store = cls(clock)
        store.put("a", {"v": 1}, ttl=10)
        store.put("b", {"v": 2}, ttl=20)
        clock.advance(15)
        results[name] = (store.count(), store.sum("v"))
    return results, "(count, sum) over live+expired"


def scenario_iterator():
    """keys/values/items/iter must hide expired rows"""
    results = {}
    for name, cls in (("buggy", BuggyStore), ("fixed", FixedStore)):
        clock = FakeClock()
        store = cls(clock)
        store.put("a", 1, ttl=10)
        clock.advance(15)
        results[name] = (list(store.keys()), list(store.values()),
                         list(store.items()), "a" in store, len(store))
    return results, "keys/values/items/contains/len after expiry"


def scenario_born_expired():
    """ttl <= 0 means the entry is expired the instant it is written"""
    results = {}
    for name, cls in (("buggy", BuggyStore), ("fixed", FixedStore)):
        clock = FakeClock()
        store = cls(clock)
        store.put("z", 3, ttl=0)
        results[name] = (store.get_many(["z"]), store.count())
    return results, "put(z, ttl=0) then (get_many, count)"


def scenario_rollback():
    """clock moved backwards cannot resurrect data observed as expired;
    explicit renewal (touch) can"""
    results = {}
    for name, cls in (("buggy", BuggyStore), ("fixed", FixedStore)):
        clock = FakeClock()
        store = cls(clock)
        store.put("a", {"v": 1}, ttl=10)
        store.put("b", {"v": 2}, ttl=10)
        clock.advance(15)
        seen = [k for k, _ in store.scan()]
        clock.rollback(20)
        after_rollback = store.get("a")
        store.touch("b", 10)
        after_renew = store.get("b")
        results[name] = (seen, after_rollback, after_renew)
    return results, "scan while expired / get after rollback / renewed"


def scenario_purge_decoupled():
    """reads stay correct even when the cleanup task never runs;
    physical cleanup is a separate operation"""
    results = {}
    for name, cls in (("buggy", BuggyStore), ("fixed", FixedStore)):
        clock = FakeClock()
        store = cls(clock)
        store.put("k", 1, ttl=10)
        clock.advance(15)
        before_purge = (store.count(), store.physical_size())
        purged = store.purge_expired()
        after_purge = (store.count(), store.physical_size())
        results[name] = (before_purge, purged, after_purge)
    return results, "no janitor running: (logical,physical) / purged / after"


SCENARIOS = [
    scenario_point_get,
    scenario_scan,
    scenario_batch_get,
    scenario_aggregate,
    scenario_iterator,
    scenario_born_expired,
    scenario_rollback,
    scenario_purge_decoupled,
]


def main():
    print("%-46s | %-34s | %-34s" % ("scenario", "BEFORE (buggy)", "AFTER (fixed)"))
    print("-" * 122)
    for build in SCENARIOS:
        results, desc = build()
        print("%-46s | %-34r | %-34r" % (desc, results["buggy"], results["fixed"]))


if __name__ == "__main__":
    main()
