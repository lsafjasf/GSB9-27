"""End-to-end scenario demo: normal polling, fetch failure (degraded
service), version rollback (rejected), and the exactly-expired boundary.

Run: python3 demo.py
"""

from keycache import KeyCache, KeyMaterial, NoUsableKeyError


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


class ScriptedSource:
    """Simulates the external key service with a time-based script."""

    def __init__(self, clock: FakeClock, script: dict) -> None:
        self._clock = clock
        self._script = script

    def __call__(self) -> KeyMaterial:
        item = self._script[self._clock.t]
        if isinstance(item, Exception):
            raise item
        return item


def main() -> None:
    clock = FakeClock(0.0)
    source = ScriptedSource(
        clock,
        {
            0: KeyMaterial("k-alpha", 1, expires_at=100),
            100: KeyMaterial("k-beta", 2, expires_at=200),
            150: ConnectionError("remote 503"),          # outage starts
            200: ConnectionError("remote 503"),          # v2 expired, outage
            250: KeyMaterial("k-beta-old", 1, expires_at=400),  # rollback!
            300: KeyMaterial("k-gamma", 3, expires_at=400),     # recovered
        },
    )
    cache = KeyCache(source, now=clock)

    def poll(t: float) -> None:
        clock.t = t
        try:
            key = cache.get_key() if cache.cached is None or t >= cache.cached.expires_at else cache.refresh()
            print("t=%3d  served key=%-10s v%d  (degradation window: %.0fs)"
                  % (t, key.key, key.version, cache.degradation_window()))
        except NoUsableKeyError as exc:
            print("t=%3d  UNUSABLE: %s" % (t, exc))

    print("== timeline ==")
    poll(0)     # normal: fetch v1
    poll(100)   # exactly expired -> refetch, accept v2
    poll(150)   # fetch fails, v2 still valid -> degraded
    poll(200)   # v2 exactly expired + fetch fails -> unusable
    poll(250)   # source offers rolled-back v1 -> rejected, still unusable
    poll(300)   # source recovers with v3 -> accepted

    print("\n== version sequence (accepted) ==")
    print(cache.version_history)

    print("\n== degradation records ==")
    for e in cache.events:
        if e.kind in ("degraded", "expired"):
            print("t=%3d  %-8s %s" % (e.at, e.kind, e.reason))

    print("\n== rollback rejection records ==")
    for e in cache.rejections:
        print("t=%3d  rejected v%d: %s" % (e.at, e.version, e.reason))


if __name__ == "__main__":
    main()
