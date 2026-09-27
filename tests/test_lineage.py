"""Regression tests for stable-identifier field lineage.

Run::

    python3 -m unittest tests.test_lineage -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lineage import (  # noqa: E402
    CycleDetectedError,
    DanglingReferenceError,
    DuplicateEntityError,
    LineageError,
    LineageGraph,
    UnknownEntityError,
)


def chain(graph, *names, **uids):
    created = []
    for name in names:
        created.append(graph.add_entity(name, uid=uids.get(name)))
    return created


class RenameMoveMergeTests(unittest.TestCase):
    def setUp(self):
        self.g = LineageGraph()
        ids = chain(
            self.g,
            "src",
            "mid",
            "sink",
            src="u_src",
            mid="u_mid",
            sink="u_sink",
        )
        self.src, self.mid, self.sink = ids
        self.g.add_edge(self.src, self.mid)
        self.g.add_edge(self.mid, self.sink)

    def test_rename_keeps_full_chain(self):
        self.g.rename(self.mid, "mid_renamed")
        self.g.validate()
        self.assertEqual(self.g.upstream(self.sink), [self.src, self.mid])
        self.assertEqual(self.g.downstream(self.src), [self.mid, self.sink])

    def test_move_keeps_full_chain(self):
        self.g.move(self.mid, "another_schema")
        self.g.validate()
        self.assertEqual(self.g.upstream(self.sink), [self.src, self.mid])

    def test_rename_is_audited(self):
        old_name = self.g.get(self.mid).name
        self.g.rename(self.mid, "v2")
        self.assertEqual(self.g.get(self.mid).history[-1], ("rename", old_name, "v2"))

    def test_merge_preserves_trace_chain(self):
        # mid is merged into a new canonical field.
        canon = self.g.add_entity("canonical", uid="u_canon")
        self.g.merge_entities(self.mid, canon)
        self.g.validate()

        # Sink still traces back through the merged node to the raw source.
        upstream = self.g.upstream(self.sink)
        self.assertIn(self.src, upstream)
        self.assertIn(canon, upstream)
        self.assertEqual(set(upstream[:2]), {self.src, canon})

        # The source node now sees the full downstream blast radius.
        down = self.g.downstream(self.src)
        self.assertEqual(set(down), {canon, self.sink})

        # The old identity resolves forward to the canonical entity.
        self.assertEqual(self.g._resolve(self.mid), canon)
        self.assertEqual(self.g.get(self.mid).status, "merged")
        self.assertEqual(self.g.upstream(self.mid), self.g.upstream(canon))

    def test_rename_then_merge_then_rename(self):
        # Real-world churn: rename, merge into canonical, rename canonical.
        self.g.rename(self.mid, "mid_v2")
        canon = self.g.add_entity("canon", uid="u_canon")
        self.g.merge_entities(self.mid, canon)
        self.g.rename(canon, "canon_v3")
        self.g.move(self.src, "landing.raw")
        self.g.validate()
        self.assertEqual(self.g.upstream(self.sink), [self.src, canon])
        self.assertEqual(self.g.downstream(self.src), [canon, self.sink])


class SameNameTests(unittest.TestCase):
    def test_same_name_distinct_entities_stay_distinct(self):
        g = LineageGraph()
        a1 = g.add_entity("amount", uid="t1_amount", container="db1")
        a2 = g.add_entity("amount", uid="t2_amount", container="db2")
        out1 = g.add_entity("total", uid="t1_total")
        out2 = g.add_entity("total", uid="t2_total")
        g.add_edge(a1, out1)
        g.add_edge(a2, out2)

        self.assertEqual(g.find_by_name("amount"), [a1, a2])

        # Renaming one must not affect the other, same-named entity.
        g.rename(a1, "amount_cents")
        g.validate()
        self.assertEqual(g.upstream(out1), [a1])
        self.assertEqual(g.upstream(out2), [a2])
        self.assertEqual(g.downstream(a1), [out1])
        self.assertEqual(g.downstream(a2), [out2])

    def test_lookup_by_uid_not_name_after_reuse(self):
        g = LineageGraph()
        old = g.add_entity("x", uid="old_x")
        down = g.add_entity("y", uid="y")
        g.add_edge(old, down)
        g.rename(old, "x_legacy")
        # A brand new entity reuses the freed name "x".
        new = g.add_entity("x", uid="new_x")
        g.validate()
        self.assertEqual(g.upstream(down), [old])
        self.assertEqual(g.downstream(new), [])


class DeletionTests(unittest.TestCase):
    def test_tombstone_keeps_traceability(self):
        g = LineageGraph()
        src, mid, sink = chain(g, "s", "m", "d", s="s", m="m", d="d")
        g.add_edge(src, mid)
        g.add_edge(mid, sink)
        g.tombstone(mid, reason="field retired")
        g.validate()
        self.assertEqual(g.upstream(sink), [src, mid])
        self.assertEqual(g.downstream(src), [mid, sink])
        self.assertEqual(g.get(mid).status, "tombstoned")

    def test_hard_delete_with_edges_is_refused(self):
        g = LineageGraph()
        src, sink = chain(g, "s", "d", s="s", d="d")
        g.add_edge(src, sink)
        with self.assertRaises(LineageError):
            g.remove_entity(src)
        self.assertTrue(g.exists(src))

    def test_hard_delete_disconnected_ok(self):
        g = LineageGraph()
        src = g.add_entity("s", uid="s")
        g.remove_entity(src)
        self.assertFalse(g.exists(src))


class MultiLayerTests(unittest.TestCase):
    def _diamond(self):
        # a -> b -> d ; a -> c -> d ; d -> e
        g = LineageGraph()
        ids = {}
        for name in "abcde":
            ids[name] = g.add_entity(name, uid="n_" + name)
        g.add_edge(ids["a"], ids["b"])
        g.add_edge(ids["a"], ids["c"])
        g.add_edge(ids["b"], ids["d"])
        g.add_edge(ids["c"], ids["d"])
        g.add_edge(ids["d"], ids["e"])
        return g, ids

    def test_diamond_upstream_and_downstream(self):
        g, ids = self._diamond()
        self.assertEqual(set(g.upstream(ids["e"])), set(ids.values()) - {ids["e"]})
        self.assertEqual(set(g.downstream(ids["a"])), set(ids.values()) - {ids["a"]})

    def test_deep_chain_renames_every_level(self):
        g = LineageGraph()
        n = 50
        uids = [g.add_entity("f%d" % i, uid="u%d" % i) for i in range(n)]
        for i in range(n - 1):
            g.add_edge(uids[i], uids[i + 1])
        for i in range(1, n - 1):
            g.rename(uids[i], "renamed_%d" % i)
        g.validate()
        self.assertEqual(g.upstream(uids[-1]), uids[:-1])
        self.assertEqual(g.downstream(uids[0]), uids[1:])


class FailureModeTests(unittest.TestCase):
    def test_cycle_rejected_on_add_edge_with_path(self):
        g = LineageGraph()
        a = g.add_entity("a", uid="a")
        b = g.add_entity("b", uid="b")
        c = g.add_entity("c", uid="c")
        g.add_edge(a, b)
        g.add_edge(b, c)
        with self.assertRaises(CycleDetectedError) as ctx:
            g.add_edge(c, a)
        cycle = ctx.exception.cycle
        self.assertEqual(cycle[0], cycle[-1])
        self.assertEqual(set(cycle), {a, b, c})
        # edge must have been rolled back: graph stays acyclic
        g.validate()
        self.assertEqual(g.immediate_upstream(a), [])

    def test_self_loop_rejected(self):
        g = LineageGraph()
        a = g.add_entity("a", uid="a")
        with self.assertRaises(CycleDetectedError):
            g.add_edge(a, a)

    def test_dangling_reference_reported_with_location(self):
        g = LineageGraph()
        a = g.add_entity("a", uid="a")
        g.rename(a, "a2")
        # Simulate an externally loaded graph containing a stale reference.
        raw = g.to_dict()
        raw["edges"].append(["a", "ghost_field"])
        with self.assertRaises(DanglingReferenceError) as ctx:
            LineageGraph.from_dict(raw)
        self.assertEqual(ctx.exception.missing, "ghost_field")
        self.assertEqual(ctx.exception.edge, ("a", "ghost_field"))

    def test_missing_endpoint_on_add_edge(self):
        g = LineageGraph()
        a = g.add_entity("a", uid="a")
        with self.assertRaises(UnknownEntityError):
            g.add_edge(a, "does_not_exist")

    def test_duplicate_uid_rejected(self):
        g = LineageGraph()
        g.add_entity("a", uid="x")
        with self.assertRaises(DuplicateEntityError):
            g.add_entity("b", uid="x")

    def test_merge_self_rejected(self):
        g = LineageGraph()
        a = g.add_entity("a", uid="a")
        with self.assertRaises(LineageError):
            g.merge_entities(a, a)


class SerializationTests(unittest.TestCase):
    def test_round_trip_preserves_edges_and_history(self):
        g = LineageGraph()
        a = g.add_entity("a", uid="a", container="s1")
        b = g.add_entity("b", uid="b")
        g.add_edge(a, b)
        g.rename(b, "b2")
        g.move(a, "s2")
        restored = LineageGraph.from_dict(g.to_dict())
        self.assertEqual(restored.upstream(b), [a])
        self.assertEqual(restored.get(b).name, "b2")
        self.assertEqual(restored.get(a).container, "s2")
        self.assertEqual(restored.get(b).history[0], ("rename", "b", "b2"))

    def test_cycle_in_loaded_data_reported(self):
        g = LineageGraph()
        a = g.add_entity("a", uid="a")
        b = g.add_entity("b", uid="b")
        c = g.add_entity("c", uid="c")
        g.add_edge(a, b)
        raw = g.to_dict()
        raw["edges"].append([b, c])
        raw["edges"].append([c, a])  # closes a -> b -> c -> a
        with self.assertRaises(CycleDetectedError) as ctx:
            LineageGraph.from_dict(raw)
        self.assertEqual(set(ctx.exception.cycle), {a, b, c})
        self.assertEqual(ctx.exception.cycle[0], ctx.exception.cycle[-1])

    def test_dangling_source_side_reported(self):
        raw = {
            "version": 1,
            "entities": [{"uid": "b", "name": "b"}],
            "edges": [["ghost", "b"]],
        }
        with self.assertRaises(DanglingReferenceError) as ctx:
            LineageGraph.from_dict(raw)
        self.assertEqual(ctx.exception.missing, "ghost")


class TraceIntegrityInvariantTests(unittest.TestCase):
    """Random-walk invariant: tracing must cover every reachable node."""

    def test_trace_completeness_over_random_dag(self):
        import random

        rng = random.Random(20260928)
        g = LineageGraph()
        layers = 30
        width = 10
        grid = [
            [g.add_entity("f_%d_%d" % (i, j), uid="u_%d_%d" % (i, j))
             for j in range(width)]
            for i in range(layers)
        ]
        for i in range(1, layers):
            for j in range(width):
                parents = rng.sample(grid[i - 1], rng.randint(1, 3))
                for p in parents:
                    g.add_edge(p, grid[i][j])

        # Random renames/moves must not change reachability at all.
        snapshot = {
            uid: (g.upstream(uid), g.downstream(uid))
            for layer in grid for uid in layer
        }
        for _ in range(200):
            uid = rng.choice(rng.choice(grid))
            if rng.random() < 0.5:
                g.rename(uid, "renamed_%d" % rng.randrange(10 ** 6))
            else:
                g.move(uid, "schema_%d" % rng.randrange(5))
        g.validate()
        for layer in grid:
            for uid in layer:
                up, down = snapshot[uid]
                self.assertEqual(g.upstream(uid), up)
                self.assertEqual(g.downstream(uid), down)

        # Every leaf's upstream set equals every node reachable by edges.
        leaf = grid[-1][0]
        self.assertEqual(len(g.upstream(leaf)), len(set(g.upstream(leaf))))
        self.assertGreater(len(g.upstream(leaf)), width * (layers - 1) // 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
