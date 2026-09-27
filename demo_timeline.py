"""Prints annotated election timelines for the key scenarios.

Run:  python3 demo_timeline.py
"""

from election import Cluster, Role


def scenario_normal():
    print("=== Scenario 1: 3-node cold-start election ===")
    c = Cluster(["A", "B", "C"], seed=7)
    c.run(0.6)
    c.print_timeline(max_events=14)
    print("-> leaders: %s  writers: %s\n" % (c.leader_ids(), c.writers()))


def scenario_old_leader_returns():
    print("=== Scenario 2: partition -> new leader -> old leader returns (5 nodes) ===")
    ids = ["A", "B", "C", "D", "E"]
    c = Cluster(ids, seed=3, check_invariant=True)
    c.run(0.6)
    old = c.leader_ids()[0]
    others = [x for x in ids if x != old]
    c.partition([old, others[0]], others[1:])
    c.run(1.0)
    node = c.nodes[old]
    mid = "old leader %s: role=%s term=%d can_write=%s" % (
        old, node.role.value, node.term,
        node.can_write(node.to_local(c.now())))
    c.heal()
    c.run(0.8)
    drops = sum(1 for _t, _n, text in c.timeline if text.startswith("DROP"))
    for t, nid, text in c.timeline:
        if not text.startswith("DROP"):
            print("%10.1f ms  %-4s %s" % (t * 1000.0, nid, text))
    print("   ... (%d partition-dropped messages omitted)" % drops)
    print("-> during partition: %s" % mid)
    print("-> after heal: old leader %s role=%s term=%d; leaders=%s writers=%s"
          % (old, c.nodes[old].role.value, c.nodes[old].term,
             c.leader_ids(), c.writers()))


def scenario_split_votes():
    print("=== Scenario 3: consecutive split votes -> deterministic fallback (4 nodes) ===")
    from test_election import ConstRng
    c = Cluster(["A", "B", "C", "D"], seed=5, check_invariant=True)
    for n in c.nodes.values():
        n.rng = ConstRng(c.cfg.election_timeout_min)
        n.election_deadline = n._election_timeout()
        c._reschedule(n.id)
    c.run(1.0)
    c.print_timeline(max_events=30)
    print("-> leaders: %s term=%d\n" % (c.leader_ids(), c.nodes["A"].term))


if __name__ == "__main__":
    scenario_normal()
    scenario_old_leader_returns()
    scenario_split_votes()
