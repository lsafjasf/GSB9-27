"""Repro: expired data leaks through read paths that skip the expiry check.

Runs identical visibility checks against the buggy (pre-fix) and the fixed
implementation and prints a before/after comparison.

Run: python3 repro.py
"""

from ttlstore import TTLStore
from ttlstore_buggy import BuggyTTLStore


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def check_point_lookup(store, clock):
    store.put("a", 1, ttl=5)
    clock.t += 10
    return store.get("a") is None


def check_range_scan(store, clock):
    store.put("b", 2, ttl=5)
    clock.t += 10
    return dict(store.scan()) == {}


def check_aggregation(store, clock):
    store.put("c", 3, ttl=5)
    clock.t += 10
    return store.count() == 0


def check_iterator(store, clock):
    store.put("d", 4, ttl=5)
    clock.t += 10
    return dict(store.items()) == {}


def check_clock_rollback(store, clock):
    store.put("e", 5, ttl=5)
    clock.t += 10       # expired
    store.get("e")      # anchor: store observes the high-water timestamp
    clock.t -= 8        # wall clock rolls back before expires_at
    return store.get("e") is None and dict(store.scan()) == {}


def check_write_already_expired(store, clock):
    store.put("f", 6, ttl=0)
    store.put("g", 7, ttl=-1)
    return store.get("f") is None and store.count() == 0 and dict(store.items()) == {}


CHECKS = [
    ("point lookup (get)", check_point_lookup),
    ("range scan (scan)", check_range_scan),
    ("aggregation (count)", check_aggregation),
    ("iterator (items)", check_iterator),
    ("clock rollback", check_clock_rollback),
    ("write already expired", check_write_already_expired),
]


def run(store_cls):
    results = {}
    for name, check in CHECKS:
        clock = FakeClock()
        store = store_cls(clock=clock)
        try:
            results[name] = bool(check(store, clock))
        except Exception:
            results[name] = False
    return results


def main():
    before = run(BuggyTTLStore)
    after = run(TTLStore)
    print(f"{'check':<28} {'before fix':>10} {'after fix':>10}")
    print("-" * 50)
    for name, _ in CHECKS:
        b = "PASS" if before[name] else "FAIL (leak)"
        a = "PASS" if after[name] else "FAIL (leak)"
        print(f"{name:<28} {b:>10} {a:>10}")
    if not all(after.values()):
        raise SystemExit("fixed implementation still leaks expired data")


if __name__ == "__main__":
    main()
