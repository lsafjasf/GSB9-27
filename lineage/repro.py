"""Repro: renaming a field breaks a name-keyed lineage graph, but not the
id-keyed LineageGraph.

Run: python3 -m lineage.repro
"""

from .lineage import LineageGraph
from .naive_lineage import NaiveLineageGraph


def build_naive():
    g = NaiveLineageGraph()
    g.add_field("user_id", "raw.users")
    g.add_field("uid", "staging.users")
    g.add_field("owner_id", "mart.accounts")
    g.add_edge("user_id", "uid")
    g.add_edge("uid", "owner_id")
    return g


def build_fixed():
    g = LineageGraph()
    a = g.add_field("user_id", entity="raw.users")
    b = g.add_field("uid", entity="staging.users")
    c = g.add_field("owner_id", entity="mart.accounts")
    g.add_edge(a, b)
    g.add_edge(b, c)
    return g, a, b, c


def main():
    print("== buggy name-keyed implementation ==")
    g = build_naive()
    print("before rename, upstream of owner_id:", sorted(g.upstream("owner_id")))
    g.rename_field("uid", "user_uid")          # field gets renamed mid-flight
    broken = g.upstream("owner_id")
    print("after rename,  upstream of owner_id:", sorted(broken))
    print("BUG REPRODUCED: chain silently severed" if "user_id" not in broken
          else "unexpectedly intact")

    print()
    print("== fixed id-keyed implementation ==")
    g, a, b, c = build_fixed()
    print("before rename, upstream of owner_id:",
          sorted(g.get_field(i).name for i in g.upstream(c)))
    g.rename_field(b, "user_uid")
    g.move_field(b, "staging.users_v2")
    intact = g.upstream(c)
    print("after rename+move, upstream of owner_id:",
          sorted(g.get_field(i).name for i in intact))
    assert a in intact and b in intact, "lineage chain must survive rename/move"
    print("OK: full chain preserved")


if __name__ == "__main__":
    main()
