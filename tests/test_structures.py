"""Structural test cases: single block, straight line, diamond,
deeply nested loops, multiple exits, self loops, unreachable blocks."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dom import (parse_tac, compute_idom, dominator_sets, dominator_tree,
                 dominance_frontiers, brute_dominator_sets, brute_idom,
                 brute_dominance_frontiers)

CASES = os.path.join(os.path.dirname(__file__), "..", "cases")

EXPECT = {
    "01_single_block.tac": {
        "idom": {"entry": "entry"},
        "df": {"entry": set()},
        "unreachable": [],
    },
    "02_straight_line.tac": {
        "idom": {"entry": "entry"},
        "df": {"entry": set()},
        "unreachable": [],
    },
    "03_diamond.tac": {
        "idom": {"entry": "entry", "then": "entry", "else": "entry",
                 "join": "entry"},
        "df": {"entry": set(), "then": {"join"}, "else": {"join"},
               "join": set()},
        "unreachable": [],
    },
    "04_nested_loops.tac": {
        "idom": {"entry": "entry", "outer_head": "entry",
                 "outer_body": "outer_head", "inner_head": "outer_body",
                 "inner_body": "inner_head", "outer_next": "inner_head",
                 "exit": "outer_head"},
        "df": {"entry": set(), "outer_head": {"outer_head"},
               "outer_body": {"outer_head"},
               "inner_head": {"inner_head", "outer_head"},
               "inner_body": {"inner_head"},
               "outer_next": {"outer_head"},
               "exit": set()},
        "unreachable": [],
    },
    "05_multi_exit_selfloop_unreachable.tac": {
        "idom": {"entry": "entry", "spin": "entry", "check": "spin",
                 "exit1": "check", "exit2": "check"},
        "df": {"entry": set(), "spin": {"spin"}, "check": set(),
               "exit1": set(), "exit2": set()},
        "unreachable": ["dead1", "dead2"],
    },
    "06_entry_backedge_parser_tolerance.tac": {
        "idom": {"entry": "entry", "alias": "entry", "toexit": "alias",
                 "body": "alias", "exit": "toexit"},
        "df": {"entry": {"entry"}, "alias": {"entry"}, "toexit": set(),
               "body": {"entry", "body"}, "exit": set()},
        "unreachable": [],
    },
}


def run_case(fname):
    with open(os.path.join(CASES, fname)) as f:
        cfg = parse_tac(f.read())
    exp = EXPECT[fname]

    idom = compute_idom(cfg)
    df = dominance_frontiers(cfg, idom)
    unreachable = cfg.unreachable_names()

    assert idom == exp["idom"], (fname, "idom", idom)
    assert df == exp["df"], (fname, "df", df)
    assert unreachable == exp["unreachable"], (fname, "unreachable",
                                               unreachable)

    # unreachable blocks must not leak into the analysis
    for name in unreachable:
        assert name not in idom and name not in df

    # cross-check against brute force
    assert dominator_sets(idom) == brute_dominator_sets(cfg), fname
    assert idom == brute_idom(cfg), fname
    assert df == brute_dominance_frontiers(cfg), fname

    # sanity: dom tree children partition reachable nodes minus entry
    tree = dominator_tree(idom)
    all_children = [c for kids in tree.values() for c in kids]
    assert sorted(all_children) == sorted(n for n in idom if n != cfg.entry)

    print("PASS %s  (blocks=%d, unreachable=%d)"
          % (fname, len(cfg.order), len(unreachable)))


if __name__ == "__main__":
    for fname in sorted(EXPECT):
        run_case(fname)
    print("all structural tests passed")
