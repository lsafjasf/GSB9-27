"""Quota isolation with revocable borrowing (standard library only).

Model
-----
* The pool has a fixed integer ``capacity`` of resource units per scheduling
  tick; one tick is ``tick_ms`` wall-clock milliseconds.
* Each tenant has a ``guarantee`` (always honored; admission control ensures
  sum(guarantees) <= capacity) and a ``limit`` (hard ceiling).
* Allocation is recomputed every tick from the current demand vector only,
  so the result never depends on the order in which requests arrived.

Allocation per tick
-------------------
1. Guarantee phase: every tenant receives min(demand, guarantee).  This
   always fits because admission control keeps sum(guarantees) <= capacity.
2. Borrow phase: leftover capacity (idle guarantees + unreserved slack) is
   distributed by deterministic max-min fair water-filling, capped so no
   tenant exceeds its limit.  Ties break on sorted tenant id.

Reclaim
-------
Every grant is a lease of exactly one tick.  When a lender's demand returns,
the guarantee phase restores its full guarantee in the very same tick and
borrowers are cut back to the remaining slack.  Worst-case reclaim latency
is therefore bounded by one scheduling quantum (``tick_ms``).
"""

from dataclasses import dataclass


class QuotaError(Exception):
    """Raised on invalid tenant configuration or admission failure."""


def max_min_fair(capacity, caps):
    """Distribute ``capacity`` integer units under per-tenant caps.

    Deterministic max-min fair water-filling: all contenders are raised
    equally, capped tenants drop out, leftovers are redistributed, and
    remainder units go to the lexicographically smallest tenant ids.
    The result depends only on (capacity, caps), never on input ordering.
    """
    alloc = {tid: 0 for tid in caps}
    active = sorted(tid for tid in caps if caps[tid] > 0)
    remaining = capacity
    while remaining > 0 and active:
        share, extra = divmod(remaining, len(active))
        still_active = []
        for idx, tid in enumerate(active):
            want = share + (1 if idx < extra else 0)
            give = min(want, caps[tid] - alloc[tid])
            alloc[tid] += give
            remaining -= give
            if alloc[tid] < caps[tid]:
                still_active.append(tid)
        active = still_active
    return alloc


@dataclass
class Tenant:
    tid: str
    guarantee: int
    limit: int
    last_grant: int = 0


@dataclass
class ReclaimEvent:
    tick: int
    tid: str
    revoked_units: int      # borrowed units taken back from other tenants
    latency_ticks: int      # ticks waited after the demanding tick (0 = same tick)
    latency_ms: float


class QuotaPool:
    def __init__(self, capacity, tick_ms=10):
        if capacity <= 0:
            raise QuotaError("capacity must be positive")
        self.capacity = capacity
        self.tick_ms = tick_ms
        self.tenants = {}
        self.tick_count = 0
        self.reclaim_events = []
        self.stats = {}  # tid -> {"demanded": int, "granted": int}
        self._prev_demands = {}
        self._prev_grants = {}

    # -- tenant lifecycle -------------------------------------------------

    def reserved(self):
        return sum(t.guarantee for t in self.tenants.values())

    def add_tenant(self, tid, guarantee, limit):
        if tid in self.tenants:
            raise QuotaError("duplicate tenant %r" % tid)
        if not (0 <= guarantee <= limit):
            raise QuotaError("need 0 <= guarantee <= limit")
        if self.reserved() + guarantee > self.capacity:
            raise QuotaError(
                "admission denied: sum(guarantees)=%d + %d > capacity=%d"
                % (self.reserved(), guarantee, self.capacity))
        self.tenants[tid] = Tenant(tid, guarantee, limit)
        self.stats[tid] = {"demanded": 0, "granted": 0}

    def remove_tenant(self, tid):
        if tid not in self.tenants:
            raise QuotaError("unknown tenant %r" % tid)
        del self.tenants[tid]
        self._prev_demands.pop(tid, None)
        self._prev_grants.pop(tid, None)

    # -- allocation ---------------------------------------------------------

    def tick(self, demands):
        """Allocate one tick. ``demands``: {tid: units wanted this tick}.

        Returns {tid: units granted}.  Pure function of the demand vector:
        request arrival order cannot change the outcome.
        """
        demands = demands or {}
        unknown = set(demands) - set(self.tenants)
        if unknown:
            raise QuotaError("demands from unknown tenants: %r" % sorted(unknown))
        demand_of = {tid: max(0, demands.get(tid, 0)) for tid in self.tenants}

        # Phase 1: guarantees.  Always fits: sum(guarantees) <= capacity.
        grants = {}
        for tid in sorted(self.tenants):
            t = self.tenants[tid]
            grants[tid] = min(demand_of[tid], t.guarantee)
        remaining = self.capacity - sum(grants.values())

        # Phase 2: borrow idle capacity, capped by each tenant's limit.
        caps = {}
        for tid in sorted(self.tenants):
            t = self.tenants[tid]
            headroom = min(demand_of[tid], t.limit) - grants[tid]
            if headroom > 0:
                caps[tid] = headroom
        for tid, extra in max_min_fair(remaining, caps).items():
            grants[tid] += extra

        self._track_reclaims(grants, demand_of)
        self._assert_invariants(grants, demand_of)

        for tid, grant in grants.items():
            self.tenants[tid].last_grant = grant
            self.stats[tid]["demanded"] += demand_of[tid]
            self.stats[tid]["granted"] += grant
        self._prev_demands = demand_of
        self._prev_grants = grants
        self.tick_count += 1
        return grants

    # -- invariants ---------------------------------------------------------

    def _assert_invariants(self, grants, demand_of):
        total = 0
        for tid, grant in grants.items():
            t = self.tenants[tid]
            # INV-1: no tenant ever exceeds its limit.
            assert 0 <= grant <= t.limit, (tid, grant, t.limit)
            # Never hand out more than was demanded.
            assert grant <= demand_of[tid], (tid, grant, demand_of[tid])
            # INV-3: guarantee is honored even under full contention.
            assert grant >= min(demand_of[tid], t.guarantee), (
                tid, grant, demand_of[tid], t.guarantee)
            total += grant
        # INV-2: the pool never hands out more than its capacity.
        assert total <= self.capacity, (total, self.capacity)
        # INV-4 (admission): guarantees never oversubscribe the pool.
        assert self.reserved() <= self.capacity

    # -- reclaim metrics ----------------------------------------------------

    def _track_reclaims(self, grants, demand_of):
        borrowed_prev = self._borrowed_total(self._prev_grants)
        for tid, t in self.tenants.items():
            prev_demand = self._prev_demands.get(tid, 0)
            woke = prev_demand < t.guarantee <= demand_of[tid]
            if woke and borrowed_prev > 0:
                # The guarantee phase must have satisfied us this same tick.
                assert grants[tid] >= min(demand_of[tid], t.guarantee)
                revoked = borrowed_prev - self._borrowed_total(grants)
                self.reclaim_events.append(ReclaimEvent(
                    tick=self.tick_count, tid=tid,
                    revoked_units=revoked, latency_ticks=0,
                    latency_ms=0.0))

    def _borrowed_total(self, grants):
        return sum(max(0, grant - self.tenants[tid].guarantee)
                   for tid, grant in grants.items() if tid in self.tenants)

    def reclaim_summary(self):
        """Aggregate reclaim latency stats (bound: 1 tick = tick_ms)."""
        events = self.reclaim_events
        if not events:
            return {"events": 0, "max_latency_ticks": 0, "max_latency_ms": 0.0,
                    "avg_latency_ms": 0.0, "total_revoked_units": 0,
                    "bound_ms": float(self.tick_ms)}
        lat = [e.latency_ticks for e in events]
        return {
            "events": len(events),
            "max_latency_ticks": max(lat),
            "max_latency_ms": max(lat) * self.tick_ms,
            "avg_latency_ms": (sum(lat) / len(lat)) * self.tick_ms,
            "total_revoked_units": sum(e.revoked_units for e in events),
            "bound_ms": float(self.tick_ms),
        }
