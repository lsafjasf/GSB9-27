"""Clocks. Time is always injected: the engine never calls time.time() itself."""
import time


class FakeClock:
    """Deterministic clock for tests. Time only moves when the engine
    advances it, so tests can assert exact timelines and wall-time bounds."""

    def __init__(self, start=0.0):
        self._t = start

    def now(self):
        return self._t

    def advance(self, dt):
        assert dt >= 0, "time cannot go backwards"
        self._t += dt


class SystemClock:
    """Real clock for production use; the same engine runs unchanged."""

    def now(self):
        return time.monotonic()

    def advance(self, dt):
        time.sleep(dt)
