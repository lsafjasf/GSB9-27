"""Self-tests for the lease election library (stdlib unittest only).

Run:  python3 test_election.py -v
"""

import unittest

from election import Cluster, Config, Role


class ConstRng:
    """Rng stub that always returns the same timeout (forces split votes)."""

    def __init__(self, value):
        self.value = value

    def uniform(self, a, b):
        return self.value


def local_can_write(cluster, nid):
    node = cluster.nodes[nid]
    return node.can_write(node.to_local(cluster.now()))


class ElectionTests(unittest.TestCase):

    def test_single_node(self):
        """A 1-node cluster elects itself and can write immediately."""
        c = Cluster(["A"], seed=1)
        c.run(1.0)
        self.assertEqual(c.leader_ids(), ["A"])
        self.assertEqual(c.writers(), ["A"])
        self.assertEqual(c.nodes["A"].term, 1)

    def test_even_cluster(self):
        """4 nodes (even): exactly one leader, one term, quorum writes."""
        c = Cluster(["A", "B", "C", "D"], seed=2, check_invariant=True)
        c.run(2.0)
        leaders = c.leader_ids()
        self.assertEqual(len(leaders), 1)
        terms = {n.term for n in c.nodes.values()}
        self.assertEqual(len(terms), 1)          # everyone converged
        self.assertEqual(c.writers(), leaders)   # exactly one writer
        self.assertEqual(len(c.leaders_by_term), 1)

    def test_majority_unavailable(self):
        """5 nodes, 3 crash: remaining minority can never elect or write."""
        c = Cluster(["A", "B", "C", "D", "E"], seed=3, check_invariant=True)
        c.run(1.0)
        self.assertEqual(len(c.leader_ids()), 1)
        alive = ["D", "E"]
        for nid in ["A", "B", "C"]:
            c.crash(nid)
        c.run(2.0)
        self.assertEqual(c.leader_ids(), [])     # no leader in minority
        self.assertEqual(c.writers(), [])        # nobody may write
        # minority keeps trying: terms keep growing, but no leader is chosen
        self.assertTrue(all(c.nodes[n].term >= 2 for n in alive))
        self.assertEqual(len(c.leaders_by_term), 1)  # still only the old term

    def test_old_leader_returns(self):
        """Split-brain protection: partitioned old leader loses its write
        lease, a new leader is elected by the majority, and the old leader
        abdicates when it rejoins (higher term forces step-down)."""
        ids = ["A", "B", "C"]
        c = Cluster(ids, seed=4, check_invariant=True)
        c.run(1.0)
        old = c.leader_ids()[0]
        old_term = c.nodes[old].term
        others = [x for x in ids if x != old]

        # partition the old leader away from the majority
        c.partition([old], others)
        c.run(1.0)

        # old leader still *thinks* it is leader, but its write lease died
        self.assertEqual(c.nodes[old].role, Role.LEADER)
        self.assertFalse(local_can_write(c, old))
        # majority side elected a new leader with a higher term
        new_leaders = [n for n in c.leader_ids() if n != old]
        self.assertEqual(len(new_leaders), 1)
        new = new_leaders[0]
        self.assertGreater(c.nodes[new].term, old_term)
        # at every simulated instant at most one node could write
        # (enforced continuously by check_invariant=True above)

        # heal: the stale leader must abdicate
        c.heal()
        c.run(1.0)
        self.assertEqual(c.nodes[old].role, Role.FOLLOWER)
        self.assertEqual(c.nodes[old].term, c.nodes[new].term)
        self.assertEqual(c.leader_ids(), [new])
        self.assertEqual(c.writers(), [new])

    def test_consecutive_election_failures(self):
        """All nodes time out simultaneously -> repeated split votes.
        After max_consecutive_failures rounds the deterministic priority
        fallback must elect the lowest-rank node; terms stay monotonic."""
        cfg = Config()
        c = Cluster(["A", "B", "C", "D"], config=cfg, seed=5,
                    check_invariant=True)
        for n in c.nodes.values():
            n.rng = ConstRng(cfg.election_timeout_min)
            n.election_deadline = n._election_timeout()
            c._reschedule(n.id)
        c.run(3.0)
        leaders = c.leader_ids()
        self.assertEqual(leaders, ["A"])         # rank 0 wins the fallback
        # F+1 rounds fail (terms 1..F+1), the priority timeout then elects
        # rank 0 in term F+2.
        self.assertEqual(c.nodes["A"].term,
                         cfg.max_consecutive_failures + 2)
        self.assertEqual(len(c.leaders_by_term), 1)  # exactly one leader total
        self.assertEqual(c.writers(), ["A"])

    def test_clock_skew_tolerance(self):
        """With clocks drifting at the tolerated bound (+/-1%), the
        no-double-writer invariant still holds through a partition+heal."""
        ids = ["A", "B", "C", "D", "E"]
        skews = {"A": -0.01, "B": -0.005, "C": 0.0, "D": 0.005, "E": 0.01}
        c = Cluster(ids, seed=6, skews=skews, check_invariant=True)
        c.run(1.0)
        old = c.leader_ids()[0]
        others = [x for x in ids if x != old]
        c.partition([old, others[0]], others[1:])
        c.run(1.5)
        self.assertFalse(local_can_write(c, old))
        self.assertEqual(len([n for n in c.leader_ids() if n != old]), 1)
        c.heal()
        c.run(1.0)
        self.assertEqual(len(c.leader_ids()), 1)
        self.assertEqual(c.nodes[old].role, Role.FOLLOWER)
        self.assertEqual(len(c.writers()), 1)

    def test_crash_restart_rejoins(self):
        """A crashed node restarts, keeps its persistent term, and rejoins."""
        c = Cluster(["A", "B", "C"], seed=7, check_invariant=True)
        c.run(1.0)
        victim = "C"
        c.crash(victim)
        c.run(0.5)
        c.restart(victim)
        c.run(1.0)
        self.assertEqual(len(c.leader_ids()), 1)
        self.assertEqual(c.nodes[victim].role, Role.FOLLOWER)
        terms = {n.term for n in c.nodes.values()}
        self.assertEqual(len(terms), 1)


if __name__ == "__main__":
    unittest.main()
