"""Lease-based leader election with monotonic terms (Raft-style).

Standard library only. Time and network are injectable:

- Node logic never reads the wall clock. Every callback receives ``now``
  expressed in the node's *own* local time base; a per-node ``clock_skew``
  models bounded clock drift so tests can verify the lease/drift math.
- ``Cluster`` is an in-memory discrete-event network with controllable
  message delays, partitions, crashes and restarts. A production wiring
  only has to provide ``send(src, dst, msg)`` and drive the node timers.

Safety model
------------
* Terms are monotonically increasing; any message carrying a higher term
  forces the receiver back to follower (this is what makes a stale leader
  abdicate when it rejoins).
* A follower that recently heard from a leader refuses to vote until its
  local ``lease_duration`` has elapsed.
* A leader may serve a write only while a majority of nodes acked a
  heartbeat within the last ``write_lease`` (leader-local time).
  ``write_lease`` is deliberately shorter than the follower-side
  ``lease_duration`` by a 2*rho drift margin, so even with clocks drifting
  at up to +/-rho the old leader's write lease always expires (in real
  time) before any majority node can vote for a new leader.
* Election timeouts are randomised in [T, 2T); after
  ``max_consecutive_failures`` failed rounds a node falls back to a
  deterministic priority timeout (rank * priority_slot), which gives a
  hard worst-case election-time bound.
"""

import enum
import heapq
import itertools
import random
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------- clocks

class ManualClock:
    """Deterministic clock driven by the test / simulation."""

    def __init__(self, t=0.0):
        self._t = float(t)

    def now(self):
        return self._t

    def advance_to(self, t):
        assert t >= self._t, "time cannot go backwards"
        self._t = float(t)

    def advance(self, dt):
        assert dt >= 0
        self._t += float(dt)


class SystemClock:
    """Wall-clock wrapper for production wiring."""

    def now(self):
        return time.monotonic()


# ---------------------------------------------------------------- config

@dataclass(frozen=True)
class Config:
    election_timeout_min: float = 0.150   # must be >= lease_duration
    election_timeout_max: float = 0.300
    heartbeat_interval: float = 0.050
    lease_duration: float = 0.150         # follower-side vote refusal window
    max_clock_drift: float = 0.01         # rho: tolerated relative drift
    max_consecutive_failures: int = 3     # failed rounds before priority fallback
    priority_slot: float = 0.020          # Delta, must be >= 2 * max one-way delay
    message_delay_min: float = 0.001
    message_delay_max: float = 0.005

    @property
    def write_lease(self):
        """Leader-side write lease, shortened by a 2*rho drift margin."""
        r = self.max_clock_drift
        return self.lease_duration * (1.0 - 2.0 * r) / (1.0 + 2.0 * r)


# ---------------------------------------------------------------- messages

@dataclass(frozen=True)
class RequestVote:
    term: int
    candidate: str


@dataclass(frozen=True)
class VoteReply:
    term: int
    granted: bool
    voter: str


@dataclass(frozen=True)
class Heartbeat:
    term: int
    leader: str


@dataclass(frozen=True)
class Ack:
    term: int
    granted: bool
    follower: str


# ---------------------------------------------------------------- roles

class Role(enum.Enum):
    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"


# ---------------------------------------------------------------- node

class Node:
    """One election participant. All times are in the node's local base."""

    def __init__(self, node_id, all_ids, config, rng=None, clock_skew=0.0):
        self.id = node_id
        self.ids = sorted(all_ids)
        self.peers = [i for i in self.ids if i != node_id]
        self.cfg = config
        self.rng = rng or random.Random()
        self.skew = clock_skew            # local = real * (1 + skew)
        # persistent state (would live on stable storage)
        self.term = 0
        self.voted_for = None
        # volatile state
        self.role = Role.FOLLOWER
        self.votes = set()
        self.ack_times = {}               # leader: voter -> local time of last ack
        self.lease_expiry = 0.0           # follower: refuse votes before this
        self.election_deadline = float("inf")
        self.heartbeat_due = float("inf")
        self.consecutive_failures = 0
        self.alive = True
        self._write_lease_valid = False

    # -- clock conversion -------------------------------------------------

    def to_local(self, real_t):
        return real_t * (1.0 + self.skew)

    def to_real(self, local_t):
        return local_t / (1.0 + self.skew)

    # -- helpers ----------------------------------------------------------

    @property
    def majority(self):
        return len(self.ids) // 2 + 1

    def rank(self):
        return self.ids.index(self.id)

    def start(self, now):
        self._reset_election_deadline(now)

    def restart(self, now):
        """Crash recovery: persistent term/vote survive, the rest resets."""
        self.alive = True
        self.role = Role.FOLLOWER
        self.votes = set()
        self.ack_times = {}
        self.lease_expiry = 0.0
        self.consecutive_failures = 0
        self._write_lease_valid = False
        self._reset_election_deadline(now)

    def _election_timeout(self):
        if self.consecutive_failures >= self.cfg.max_consecutive_failures:
            # deterministic fallback: lowest-rank live node fires first
            return self.cfg.election_timeout_min + self.rank() * self.cfg.priority_slot
        return self.rng.uniform(self.cfg.election_timeout_min,
                                self.cfg.election_timeout_max)

    def _reset_election_deadline(self, now):
        self.election_deadline = now + self._election_timeout()

    # -- state transitions --------------------------------------------------

    def _become_follower(self, now, term, cluster):
        stepped_down = self.role is Role.LEADER
        self.term = term
        self.role = Role.FOLLOWER
        self.voted_for = None
        self.votes = set()
        self.ack_times = {}
        self._write_lease_valid = False
        self._reset_election_deadline(now)
        if stepped_down:
            cluster.log(self.id, "steps down to follower (term=%d)" % term)

    def _start_election(self, now, cluster):
        if self.role is Role.CANDIDATE:
            self.consecutive_failures += 1
        self.term += 1
        self.role = Role.CANDIDATE
        self.voted_for = self.id
        self.votes = {self.id}
        self._reset_election_deadline(now)
        cluster.log(self.id, "starts election term=%d (consecutive failures=%d)"
                    % (self.term, self.consecutive_failures))
        if len(self.votes) >= self.majority:      # single-node cluster
            self._become_leader(now, cluster)
            return
        for peer in self.peers:
            cluster.send(self.id, peer, RequestVote(self.term, self.id))

    def _become_leader(self, now, cluster):
        self.role = Role.LEADER
        self.consecutive_failures = 0
        # the votes just received count as fresh majority acks
        self.ack_times = {v: now for v in self.votes}
        self._write_lease_valid = True
        cluster.note_leader(self.id, self.term)
        cluster.log(self.id, "becomes LEADER term=%d" % self.term)
        self._send_heartbeats(now, cluster)

    def _send_heartbeats(self, now, cluster):
        self.ack_times[self.id] = now
        self.heartbeat_due = now + self.cfg.heartbeat_interval
        for peer in self.peers:
            cluster.send(self.id, peer, Heartbeat(self.term, self.id))

    # -- write gate ---------------------------------------------------------

    def can_write(self, now):
        """True only while a majority acked within the local write lease."""
        if not (self.alive and self.role is Role.LEADER):
            return False
        recent = sum(1 for t in self.ack_times.values()
                     if now - t <= self.cfg.write_lease)
        return recent >= self.majority

    # -- message handlers ---------------------------------------------------

    def on_message(self, now, msg, cluster):
        if not self.alive:
            return
        if isinstance(msg, RequestVote):
            self._on_request_vote(now, msg, cluster)
        elif isinstance(msg, VoteReply):
            self._on_vote_reply(now, msg, cluster)
        elif isinstance(msg, Heartbeat):
            self._on_heartbeat(now, msg, cluster)
        elif isinstance(msg, Ack):
            self._on_ack(now, msg, cluster)

    def _on_request_vote(self, now, msg, cluster):
        if msg.term > self.term:
            self._become_follower(now, msg.term, cluster)
        grant = (msg.term == self.term
                 and self.voted_for in (None, msg.candidate)
                 and now >= self.lease_expiry)
        if grant:
            self.voted_for = msg.candidate
            self._reset_election_deadline(now)
            cluster.log(self.id, "grants vote to %s (term=%d)"
                        % (msg.candidate, msg.term))
        else:
            cluster.log(self.id, "rejects vote for %s (term=%d, voted_for=%s)"
                        % (msg.candidate, msg.term, self.voted_for))
        cluster.send(self.id, msg.candidate, VoteReply(self.term, grant, self.id))

    def _on_vote_reply(self, now, msg, cluster):
        if msg.term > self.term:
            self._become_follower(now, msg.term, cluster)
            return
        if self.role is Role.CANDIDATE and msg.granted and msg.term == self.term:
            self.votes.add(msg.voter)
            if len(self.votes) >= self.majority:
                self._become_leader(now, cluster)

    def _on_heartbeat(self, now, msg, cluster):
        if msg.term >= self.term:
            if msg.term > self.term or self.role is not Role.FOLLOWER:
                self._become_follower(now, msg.term, cluster)
            self.lease_expiry = now + self.cfg.lease_duration
            self.consecutive_failures = 0
            self._reset_election_deadline(now)
            cluster.send(self.id, msg.leader, Ack(self.term, True, self.id))
        else:
            # stale leader: tell it about the newer term so it abdicates
            cluster.send(self.id, msg.leader, Ack(self.term, False, self.id))

    def _on_ack(self, now, msg, cluster):
        if msg.term > self.term:
            cluster.log(self.id, "sees higher term %d in Ack, abdicates" % msg.term)
            self._become_follower(now, msg.term, cluster)
            return
        if self.role is Role.LEADER and msg.granted and msg.term == self.term:
            self.ack_times[msg.follower] = now

    # -- timers ---------------------------------------------------------------

    def on_timer(self, now, cluster):
        if not self.alive:
            return
        if self.role is Role.LEADER:
            valid = self.can_write(now)
            if self._write_lease_valid and not valid:
                cluster.log(self.id, "write lease EXPIRED (no majority acks)")
            self._write_lease_valid = valid
            if now >= self.heartbeat_due:
                self._send_heartbeats(now, cluster)
        elif now >= self.election_deadline:
            self._start_election(now, cluster)

    def next_wakeup(self):
        """Next local time at which on_timer must run."""
        if not self.alive:
            return float("inf")
        if self.role is Role.LEADER:
            wake = self.heartbeat_due
            if self._write_lease_valid:
                times = sorted(self.ack_times.values(), reverse=True)
                if len(times) >= self.majority:
                    expiry = times[self.majority - 1] + self.cfg.write_lease + 1e-6
                    wake = min(wake, expiry)
            return wake
        return self.election_deadline


# ---------------------------------------------------------------- cluster

class Cluster:
    """In-memory discrete-event network + clock for tests and simulations."""

    def __init__(self, ids, config=None, seed=0, skews=None, check_invariant=False):
        self.cfg = config or Config()
        self.clock = ManualClock()
        self.rng = random.Random(seed)
        skews = skews or {}
        self.nodes = {
            nid: Node(nid, ids, self.cfg,
                      rng=random.Random((seed + 1) * 7919 + idx),
                      clock_skew=skews.get(nid, 0.0))
            for idx, nid in enumerate(ids)
        }
        self.events = []                 # heap of (time, seq, kind, payload)
        self._seq = itertools.count()
        self.blocked = set()             # (src, dst) pairs dropped by partition
        self.timeline = []               # (real_time, node_id, text)
        self.check_invariant = check_invariant
        self._timer_keys = {}
        self.leaders_by_term = {}        # term -> leader id (uniqueness check)
        for nid in ids:
            self.nodes[nid].start(0.0)
            self._reschedule(nid)

    # -- infrastructure -----------------------------------------------------

    def now(self):
        return self.clock.now()

    def log(self, nid, text):
        self.timeline.append((self.clock.now(), nid, text))

    def note_leader(self, nid, term):
        prev = self.leaders_by_term.setdefault(term, nid)
        assert prev == nid, "two leaders in term %d: %s and %s" % (term, prev, nid)

    def send(self, src, dst, msg):
        if (src, dst) in self.blocked:
            self.log(src, "DROP %s -> %s (partition)"
                     % (type(msg).__name__, dst))
            return
        if not (self.nodes[src].alive and self.nodes[dst].alive):
            return
        delay = self.rng.uniform(self.cfg.message_delay_min,
                                 self.cfg.message_delay_max)
        heapq.heappush(self.events,
                       (self.clock.now() + delay, next(self._seq), "msg", (dst, msg)))

    def _reschedule(self, nid):
        node = self.nodes[nid]
        wake = node.to_real(node.next_wakeup())
        self._timer_keys[nid] = wake
        heapq.heappush(self.events, (wake, next(self._seq), "timer", nid))

    # -- fault injection ------------------------------------------------------

    def partition(self, group_a, group_b):
        for a in group_a:
            for b in group_b:
                self.blocked.add((a, b))
                self.blocked.add((b, a))
        self.log("sys", "PARTITION %s | %s" % (sorted(group_a), sorted(group_b)))

    def heal(self):
        self.blocked.clear()
        self.log("sys", "HEAL partition")

    def crash(self, nid):
        self.nodes[nid].alive = False
        self.log("sys", "CRASH %s" % nid)

    def restart(self, nid):
        node = self.nodes[nid]
        node.restart(node.to_local(self.now()))
        self._reschedule(nid)
        self.log("sys", "RESTART %s" % nid)

    # -- driving time ---------------------------------------------------------

    def run_until(self, t):
        while self.events and self.events[0][0] <= t:
            et, _, kind, payload = heapq.heappop(self.events)
            self.clock.advance_to(et)
            if kind == "msg":
                dst, msg = payload
                node = self.nodes[dst]
                node.on_message(node.to_local(et), msg, self)
                self._reschedule(dst)
            else:
                node = self.nodes[payload]
                if self._timer_keys.get(payload) == et:
                    node.on_timer(node.to_local(et), self)
                self._reschedule(payload)
            if self.check_invariant:
                writers = self.writers()
                assert len(writers) <= 1, \
                    "t=%.6f: SAFETY VIOLATION, multiple writers %s" % (et, writers)

    def run(self, dt):
        self.run_until(self.clock.now() + dt)

    # -- queries ----------------------------------------------------------------

    def leader_ids(self):
        return [n.id for n in self.nodes.values()
                if n.alive and n.role is Role.LEADER]

    def writers(self):
        return [n.id for n in self.nodes.values()
                if n.can_write(n.to_local(self.now()))]

    def print_timeline(self, max_events=None):
        events = self.timeline if max_events is None else self.timeline[:max_events]
        for t, nid, text in events:
            print("%10.1f ms  %-4s %s" % (t * 1000.0, nid, text))
