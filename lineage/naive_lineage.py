""" buggy lineage implementation (kept for the repro case).

References between fields are stored BY NAME. Renaming a field silently
breaks the chain: downstream fields still point at the old name, which no
longer resolves, and upstream() quietly returns nothing.
"""


class NaiveLineageGraph:
    def __init__(self):
        self.fields = {}          # name -> {"path": str}
        self.edges = {}           # downstream name -> set of upstream names

    def add_field(self, name, path=""):
        self.fields[name] = {"path": path}
        self.edges.setdefault(name, set())

    def rename_field(self, old_name, new_name):
        # BUG: only the field itself is renamed; edges keyed by old_name
        # are left dangling and nothing is updated or reported.
        self.fields[new_name] = self.fields.pop(old_name)
        self.edges[new_name] = self.edges.pop(old_name, set())

    def add_edge(self, upstream_name, downstream_name):
        self.edges.setdefault(downstream_name, set()).add(upstream_name)

    def upstream(self, name):
        seen, stack = set(), [name]
        while stack:
            cur = stack.pop()
            for up in self.edges.get(cur, ()):
                if up not in seen and up in self.fields:  # silently drops dangling refs
                    seen.add(up)
                    stack.append(up)
        return seen
