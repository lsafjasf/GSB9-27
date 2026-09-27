"""Demo application types used by the deserializer demo and tests.

CONSTRUCTED is a side-effect log: every object construction is recorded so
tests can prove whether an unauthorized type was instantiated or not.
"""

CONSTRUCTED = []


def reset_log():
    del CONSTRUCTED[:]


class Vector:
    """Whitelisted, harmless type."""

    def __init__(self, x=0, y=0):
        CONSTRUCTED.append(("Vector", x, y))
        self.x = x
        self.y = y

    @classmethod
    def from_pair(cls, pair):
        return cls(pair[0], pair[1])

    def __repr__(self):
        return f"Vector({self.x!r}, {self.y!r})"


class User:
    """Whitelisted type, but carries a dangerous legacy classmethod."""

    def __init__(self, name=""):
        CONSTRUCTED.append(("User", name))
        self.name = name

    @classmethod
    def from_legacy(cls, blob):
        """Legacy 'custom constructor': secretly builds ARBITRARY types."""
        import importlib

        module_name, _, qualname = blob["type"].rpartition(".")
        target = getattr(importlib.import_module(module_name), qualname)
        return target(*blob.get("args", []))

    def __repr__(self):
        return f"User({self.name!r})"


class ShellCommand:
    """Unauthorized type. Must NEVER be constructed by the deserializer."""

    def __init__(self, cmd=""):
        CONSTRUCTED.append(("ShellCommand", cmd))
        self.cmd = cmd

    def __repr__(self):
        return f"ShellCommand({self.cmd!r})"
