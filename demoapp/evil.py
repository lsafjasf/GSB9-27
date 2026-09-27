"""Unauthorized types. Construction of anything here is a security breach.

Every constructor/factory appends to CONSTRUCTED so tests and the bypass
suite can prove whether an unauthorized object was materialized.
"""

CONSTRUCTED = []


class Backdoor:
    def __init__(self, **kwargs):
        CONSTRUCTED.append(("Backdoor", kwargs))

    def __repr__(self):
        return "Backdoor()"


def backdoor_factory(*args, **kwargs):
    CONSTRUCTED.append(("backdoor_factory", args, kwargs))
    return Backdoor()
