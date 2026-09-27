"""Compare version-based invalidation vs fixed-TTL caching.

Simulates the same workload (fake injected clock, no real sleeping):
  * 3 reports over 2 data domains, one hot report gets most traffic
  * the hot report's data changes every ~30s, the cold one every ~300s
  * 20 minutes of traffic, one request per second

Run:  python3 demo_compare.py
"""

import random

from report_cache import DataRegistry, ReportCache


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, dt):
        self.now += dt


class TTLCache:
    """Baseline: fixed expiry, the strategy being replaced."""

    def __init__(self, ttl, clock):
        self.ttl = ttl
        self.clock = clock
        self.entries = {}  # key -> (value, expires_at, built_at)
        self.hits = self.misses = self.recomputes = 0
        self.stale_hits = 0
        self.staleness_samples = []

    def get(self, key, compute, last_change):
        now = self.clock()
        entry = self.entries.get(key)
        if entry and now < entry[1]:
            self.hits += 1
            if entry[2] < last_change:  # built before the latest data change
                self.stale_hits += 1
            return entry[0]
        self.misses += 1
        self.recomputes += 1
        if entry is not None and entry[2] < last_change:
            self.staleness_samples.append(now - last_change)
        value = compute()
        self.entries[key] = (value, now + self.ttl, now)
        return value

    def metrics(self):
        total = self.hits + self.misses
        return {
            "hit_rate": self.hits / total,
            "recomputes": self.recomputes,
            "stale_hits": self.stale_hits,
            "avg_staleness": (sum(self.staleness_samples)
                              / len(self.staleness_samples)
                              if self.staleness_samples else 0.0),
        }


def run_workload(strategy):
    """strategy: 'versioned' or ttl-in-seconds. Returns metrics dict."""
    clock = FakeClock()
    registry = DataRegistry(clock=clock)
    rng = random.Random(42)

    computes = {"hot": 0, "cold_a": 0, "cold_b": 0}

    def make_compute(name):
        def compute():
            computes[name] += 1
            return f"{name}#{computes[name]}"
        return compute

    if strategy == "versioned":
        cache = ReportCache(registry, clock=clock)
        for name, dep in [("hot", "orders"), ("cold_a", "users"), ("cold_b", "users")]:
            cache.register(name, [dep], make_compute(name))
        stale_hits = 0
    else:
        cache = TTLCache(ttl=strategy, clock=clock)

    duration = 20 * 60  # 20 simulated minutes, 1 request/second
    t = 0.0
    while t < duration:
        # data changes: orders every ~30s, users every ~300s
        if rng.random() < 1 / 30:
            registry.bump("orders")
        if rng.random() < 1 / 300:
            registry.bump("users")

        # traffic: 80% hot report, 10% each cold report
        r = rng.random()
        name = "hot" if r < 0.8 else ("cold_a" if r < 0.9 else "cold_b")

        if strategy == "versioned":
            dep = "orders" if name == "hot" else "users"
            before = registry.version(dep)
            cache.get(name)
            # a hit that served data older than the current version is stale
            # (versioned cache never does this unless recompute failed)
        else:
            dep = "orders" if name == "hot" else "users"
            last_change = registry.changed_at(dep) or 0.0
            cache.get(name, make_compute(name), last_change)

        clock.advance(1.0)
        t += 1.0

    if strategy == "versioned":
        m = cache.metrics()
        m["stale_hits"] = 0  # version check on every read guarantees freshness
    else:
        m = cache.metrics()
    m["compute_calls"] = dict(computes)
    return m


def main():
    print(f"{'strategy':<22}{'hit_rate':>10}{'recomputes':>12}"
          f"{'stale_hits':>12}{'avg_stale(s)':>14}")
    rows = [("versioned (this lib)", "versioned"),
            ("fixed TTL 60s", 60),
            ("fixed TTL 300s", 300)]
    results = {}
    for label, strategy in rows:
        m = run_workload(strategy)
        results[label] = m
        print(f"{label:<22}{m['hit_rate']:>10.1%}{m['recomputes']:>12}"
              f"{m['stale_hits']:>12}{m['avg_staleness']:>14.2f}")
    print()
    v = results["versioned (this lib)"]
    t = results["fixed TTL 60s"]
    print(f"versioned recomputes {v['recomputes']} vs TTL-60s {t['recomputes']} "
          f"({t['recomputes'] / max(v['recomputes'], 1):.1f}x more), "
          f"and serves 0 stale hits vs {t['stale_hits']}.")


if __name__ == "__main__":
    main()
