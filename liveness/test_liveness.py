"""Hand-written edge-case tests with exact expected live sets.

Run: python3 test_liveness.py
"""

from liveness import CFG, EXCEPTIONAL, analyze, analyze_naive


def both(cfg):
    fast = analyze(cfg)
    naive = analyze_naive(cfg)
    assert fast[0] == naive[0] and fast[1] == naive[1], "solvers disagree"
    return fast[0], fast[1]


def expect(cfg, want_in, want_out):
    live_in, live_out = both(cfg)
    for bid, want in want_in.items():
        assert live_in[bid] == set(want), \
            "block %r live_in: got %s want %s" % (bid, sorted(live_in[bid]), sorted(want))
    for bid, want in want_out.items():
        assert live_out[bid] == set(want), \
            "block %r live_out: got %s want %s" % (bid, sorted(live_out[bid]), sorted(want))


def test_empty_graph():
    cfg = CFG()
    live_in, live_out, stats = analyze(cfg)
    assert live_in == {} and live_out == {} and stats["rounds"] == 0
    li2, lo2, st2 = analyze_naive(cfg)
    assert li2 == {} and lo2 == {} and st2["rounds"] == 0


def test_single_block():
    cfg = CFG()
    cfg.add_block(0, uses={"a", "b"}, defs={"b"})
    expect(cfg, {0: {"a", "b"}}, {0: set()})


def test_forward_chain_only():
    # 0 -> 1 -> 2, pure forward edges, no loops.
    cfg = CFG()
    cfg.add_block(0, uses=set(), defs={"x"})
    cfg.add_block(1, uses={"x"}, defs={"y"})
    cfg.add_block(2, uses={"y"}, defs=set())
    cfg.add_edge(0, 1)
    cfg.add_edge(1, 2)
    expect(cfg,
           {0: set(), 1: {"x"}, 2: {"y"}},
           {0: {"x"}, 1: {"y"}, 2: set()})


def test_diamond():
    cfg = CFG()
    cfg.add_block(0, uses={"c"}, defs={"a"})
    cfg.add_block(1, uses={"a"}, defs=set())
    cfg.add_block(2, uses=set(), defs={"a"})
    cfg.add_block(3, uses={"a"}, defs=set())
    cfg.add_edge(0, 1)
    cfg.add_edge(0, 2)
    cfg.add_edge(1, 3)
    cfg.add_edge(2, 3)
    expect(cfg,
           {0: {"c"}, 1: {"a"}, 2: set(), 3: {"a"}},
           {0: {"a"}, 1: {"a"}, 2: {"a"}, 3: set()})


def test_self_loop():
    # while (x) { x = f(x) } as a single looping block.
    cfg = CFG()
    cfg.add_block(0, uses={"x"}, defs=set())
    cfg.add_edge(0, 0)
    expect(cfg, {0: {"x"}}, {0: {"x"}})


def test_loop_with_kill():
    # 0: i=0 -> 1(header): use i,cond -> 2(body): use i, def i -> back to 1
    cfg = CFG()
    cfg.add_block(0, uses=set(), defs={"i"})
    cfg.add_block(1, uses={"i", "cond"}, defs=set())
    cfg.add_block(2, uses={"i"}, defs={"i"})
    cfg.add_block(3, uses={"i"}, defs=set())
    cfg.add_edge(0, 1)
    cfg.add_edge(1, 2)
    cfg.add_edge(2, 1)
    cfg.add_edge(1, 3)
    expect(cfg,
           {0: {"cond"}, 1: {"i", "cond"}, 2: {"i", "cond"}, 3: {"i"}},
           {0: {"i", "cond"}, 1: {"i", "cond"}, 2: {"i", "cond"}, 3: set()})


def test_nested_loops():
    # Two nested loops: 0->1, 1->2, 2->1 (inner), 1->3, 3->0 (outer), 0->4 exit.
    cfg = CFG()
    cfg.add_block(0, uses={"a"}, defs=set())
    cfg.add_block(1, uses={"b"}, defs=set())
    cfg.add_block(2, uses={"c"}, defs=set())
    cfg.add_block(3, uses={"d"}, defs=set())
    cfg.add_block(4, uses=set(), defs=set())
    cfg.add_edge(0, 1)
    cfg.add_edge(1, 2)
    cfg.add_edge(2, 1)
    cfg.add_edge(1, 3)
    cfg.add_edge(3, 0)
    cfg.add_edge(0, 4)
    expect(cfg,
           {0: {"a", "b", "c", "d"}, 1: {"a", "b", "c", "d"},
            2: {"a", "b", "c", "d"}, 3: {"a", "b", "c", "d"}, 4: set()},
           {0: {"a", "b", "c", "d"}, 1: {"a", "b", "c", "d"},
            2: {"a", "b", "c", "d"}, 3: {"a", "b", "c", "d"}, 4: set()})


def test_unreachable_block():
    # Block 9 is unreachable from entry 0 but still must be analyzed.
    cfg = CFG()
    cfg.add_block(0, uses={"x"}, defs=set())
    cfg.add_block(9, uses={"u"}, defs=set())
    cfg.add_edge(9, 9)  # unreachable self-loop keeps u live forever
    expect(cfg, {0: {"x"}, 9: {"u"}}, {0: set(), 9: {"u"}})


def test_exceptional_edge():
    # 0 may throw into handler 2; handler reads e, so e is live out of 0.
    cfg = CFG()
    cfg.add_block(0, uses=set(), defs={"v"})
    cfg.add_block(1, uses={"v"}, defs=set())
    cfg.add_block(2, uses={"e"}, defs=set())
    cfg.add_edge(0, 1)
    cfg.add_edge(0, 2, EXCEPTIONAL)
    expect(cfg,
           {0: {"e"}, 1: {"v"}, 2: {"e"}},
           {0: {"e", "v"}, 1: set(), 2: set()})


def test_edge_to_undefined_block():
    # Edge to a block never declared: materialized as empty block.
    cfg = CFG()
    cfg.add_block(0, uses={"x"}, defs=set())
    cfg.add_edge(0, 1)
    expect(cfg, {0: {"x"}, 1: set()}, {0: set(), 1: set()})


def test_no_variables():
    cfg = CFG()
    cfg.add_block(0)
    cfg.add_block(1)
    cfg.add_edge(0, 1)
    cfg.add_edge(1, 0)
    expect(cfg, {0: set(), 1: set()}, {0: set(), 1: set()})


ALL = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in ALL:
        t()
        print("PASS %s" % t.__name__)
    print("OK: %d edge-case tests" % len(ALL))
