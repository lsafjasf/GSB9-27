"""Regression tests for the id-keyed lineage graph.

Run: python3 -m unittest lineage.test_lineage -v
"""

import unittest

from .lineage import (
    CycleError,
    DanglingReferenceError,
    LineageGraph,
    UnknownFieldError,
)
from .naive_lineage import NaiveLineageGraph


def chain_graph():
    """raw.a -> stg.b -> mart.c, returns (graph, a, b, c)."""
    g = LineageGraph()
    a = g.add_field("a", entity="raw.t")
    b = g.add_field("b", entity="stg.t")
    c = g.add_field("c", entity="mart.t")
    g.add_edge(a, b)
    g.add_edge(b, c)
    return g, a, b, c


class TestStableIdentity(unittest.TestCase):
    def test_rename_preserves_upstream_chain(self):
        g, a, b, c = chain_graph()
        g.rename_field(b, "b_renamed")
        self.assertEqual(g.upstream(c), {a, b})
        self.assertEqual(g.upstream_sources(c), {a})
        self.assertEqual(g.downstream(a), {b, c})

    def test_move_preserves_upstream_chain(self):
        g, a, b, c = chain_graph()
        g.move_field(b, new_entity="stg.t_v2", new_path="/dw/stg/t_v2#b")
        self.assertEqual(g.upstream(c), {a, b})
        self.assertEqual(g.get_field(b).entity, "stg.t_v2")

    def test_merge_preserves_traceability(self):
        g, a, b, c = chain_graph()
        d = g.add_field("d", entity="stg.t2")
        g.add_edge(d, b)
        # merge b into a single surviving field `b2`
        b2 = g.add_field("b2", entity="stg.unified")
        g.merge_fields(b2, b)
        self.assertEqual(g.upstream(c), {a, d, b2})
        self.assertEqual(g.upstream_sources(c), {a, d})
        self.assertEqual(g.downstream(a), {b2, c})
        self.assertRaises(UnknownFieldError, g.get_field, b)

    def test_naive_name_keyed_graph_breaks_on_rename(self):
        # documents the original bug: name-keyed edges silently sever
        g = NaiveLineageGraph()
        g.add_field("a")
        g.add_field("b")
        g.add_field("c")
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        g.rename_field("b", "b2")
        self.assertNotIn("a", g.upstream("c"))  # broken chain, no error raised


class TestTraceBothDirections(unittest.TestCase):
    def test_multi_level_upstream_and_sources(self):
        g = LineageGraph()
        ids = [g.add_field(f"f{i}") for i in range(5)]
        for up, down in zip(ids, ids[1:]):
            g.add_edge(up, down)
        self.assertEqual(g.upstream(ids[4]), set(ids[:4]))
        self.assertEqual(g.upstream_sources(ids[4]), {ids[0]})

    def test_downstream_impact(self):
        g = LineageGraph()
        root = g.add_field("root")
        mid1, mid2 = g.add_field("m1"), g.add_field("m2")
        leaf1, leaf2, leaf3 = (g.add_field(f"l{i}") for i in range(3))
        g.add_edge(root, mid1)
        g.add_edge(root, mid2)
        g.add_edge(mid1, leaf1)
        g.add_edge(mid1, leaf2)
        g.add_edge(mid2, leaf3)
        self.assertEqual(g.downstream(root), {mid1, mid2, leaf1, leaf2, leaf3})
        self.assertEqual(g.downstream_impact(root), {leaf1, leaf2, leaf3})
        self.assertEqual(g.downstream(mid1), {leaf1, leaf2})


class TestSameNameDifferentEntities(unittest.TestCase):
    def test_same_name_fields_are_independent(self):
        g = LineageGraph()
        amt_eu = g.add_field("amount", entity="eu.orders")
        amt_us = g.add_field("amount", entity="us.orders")
        out_eu = g.add_field("revenue", entity="eu.mart")
        out_us = g.add_field("revenue", entity="us.mart")
        g.add_edge(amt_eu, out_eu)
        g.add_edge(amt_us, out_us)
        self.assertEqual(g.upstream(out_eu), {amt_eu})
        self.assertEqual(g.upstream(out_us), {amt_us})
        # renaming one must not leak into the other's chain
        g.rename_field(amt_eu, "amount_eur")
        self.assertEqual(g.upstream(out_us), {amt_us})
        self.assertEqual(g.get_field(amt_us).name, "amount")


class TestDeletedEntity(unittest.TestCase):
    def test_delete_with_dependents_raises_and_locates(self):
        g, a, b, c = chain_graph()
        with self.assertRaises(DanglingReferenceError) as ctx:
            g.delete_field(b)
        msg = str(ctx.exception)
        self.assertIn(b, msg)
        self.assertIn(c, msg)  # points at the affected downstream

    def test_cascade_delete_is_explicit_and_clean(self):
        g, a, b, c = chain_graph()
        g.delete_field(b, cascade=True)
        g.validate()  # graph stays consistent, no dangling edge left
        self.assertEqual(g.upstream(c), set())
        self.assertEqual(g.downstream(a), set())

    def test_dangling_reference_from_deserialization(self):
        g = LineageGraph.from_records(
            fields=[{"field_id": "x", "name": "x"}],
            edges=[("ghost", "x")],
        )
        with self.assertRaises(DanglingReferenceError) as ctx:
            g.validate()
        self.assertIn("ghost", str(ctx.exception))


class TestCycleDetection(unittest.TestCase):
    def test_cycle_rejected_on_add_with_path(self):
        g, a, b, c = chain_graph()
        with self.assertRaises(CycleError) as ctx:
            g.add_edge(c, a)
        self.assertEqual(ctx.exception.path, [c, b, a, c])

    def test_self_loop_rejected(self):
        g = LineageGraph()
        a = g.add_field("a")
        with self.assertRaises(CycleError):
            g.add_edge(a, a)

    def test_cycle_detected_by_validate(self):
        g = LineageGraph.from_records(
            fields=[{"field_id": i, "name": i} for i in "xyz"],
            edges=[("x", "y"), ("y", "z"), ("z", "x")],
        )
        with self.assertRaises(CycleError) as ctx:
            g.validate()
        path = ctx.exception.path
        self.assertEqual(path[0], path[-1])  # cycle path closes the loop

    def test_unknown_endpoint_rejected(self):
        g = LineageGraph()
        a = g.add_field("a")
        with self.assertRaises(UnknownFieldError):
            g.add_edge(a, "missing")


if __name__ == "__main__":
    unittest.main()
