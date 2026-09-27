"""Reproduction of the production incident.

The legacy graph keyed fields by *name*.  Renaming the middle field leaves
upstream/downstream edges pointing at the old name, so the lineage silently
breaks into two pieces and upstream sources can no longer be traced.

Run::

    python3 tests/repro_broken_lineage.py

The script prints the broken trace from the legacy implementation, then shows
the fixed implementation tracing the full chain across the same rename.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lineage import LineageGraph  # noqa: E402


class NameKeyedLineage:
    """The old, broken implementation: adjacency keyed by display name."""

    def __init__(self):
        self.up = {}   # name -> [upstream names]
        self.down = {}  # name -> [downstream names]

    def add_field(self, name):
        self.up.setdefault(name, [])
        self.down.setdefault(name, [])

    def add_edge(self, src, dst):
        self.down.setdefault(src, []).append(dst)
        self.up.setdefault(dst, []).append(src)

    def rename(self, old, new):
        # Move the node's own lists to the new name and delete the old record.
        self.up[new] = self.up.pop(old)
        self.down[new] = self.down.pop(old)
        # NOTE: edges stored on *other* nodes still say `old`; nothing rewrites
        # them.  This is the bug.

    def trace_upstream(self, name, seen=None):
        seen = seen if seen is not None else []
        if name in seen:
            return seen
        seen = seen + [name]
        for parent in self.up.get(name, []):
            if parent not in seen:
                seen = self.trace_upstream(parent, seen)
        return seen


def main():
    raw = NameKeyedLineage()
    for field in ("raw.user_id", "dwd.uid", "dws.user_key"):
        raw.add_field(field)
    raw.add_edge("raw.user_id", "dwd.uid")
    raw.add_edge("dwd.uid", "dws.user_key")

    # Middle field is renamed (schema standardisation).
    raw.rename("dwd.uid", "dwd.user_id")

    trace = raw.trace_upstream("dws.user_key")
    print("[legacy name-keyed] upstream of dws.user_key after rename:")
    print("   ", trace)
    assert "raw.user_id" not in trace, (
        "demonstrating the bug: the ultimate source raw.user_id is unreachable"
    )
    print("    --> BROKEN: raw.user_id lost, chain stops at stale name dwd.uid")

    fixed = LineageGraph()
    source = fixed.add_entity("user_id", uid="u_raw", container="raw")
    middle = fixed.add_entity("uid", uid="u_dwd", container="dwd")
    target = fixed.add_entity("user_key", uid="u_dws", container="dws")
    fixed.add_edge(source, middle)
    fixed.add_edge(middle, target)

    fixed.rename(middle, "user_id")  # same rename, uid stays u_dwd
    fixed.validate()
    upstream = fixed.upstream(target)
    print("[fixed uid-keyed]   upstream of dws.user_key after rename:")
    print("   ", [(u, fixed.get(u).name) for u in upstream])
    assert upstream == [source, middle], "full chain must survive rename"
    print("    --> OK: raw.user_id -> dwd.user_id -> dws.user_key intact")


if __name__ == "__main__":
    main()
