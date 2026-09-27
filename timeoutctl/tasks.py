"""Task-tree DSL: single steps, serial sequences, retries, parallel branches."""


class Step:
    """Leaf unit of work. `duration` is how long it needs on the clock.
    `fail_times` makes the first N attempts fail (to exercise retries)."""

    def __init__(self, name, duration, fail_times=0):
        assert duration >= 0
        self.name = name
        self.duration = duration
        self.fail_times = fail_times
        self._failures_left = fail_times

    def _should_fail(self):
        if self._failures_left > 0:
            self._failures_left -= 1
            return True
        return False


class Seq:
    """Run children one after another; fail-fast on the first non-OK child."""

    def __init__(self, name, *children):
        assert children, "Seq needs at least one child"
        self.name = name
        self.children = list(children)


class Retry:
    """Run `child` again on failure, up to `attempts` times. All attempts
    draw from the SAME budget (that is the whole point of the fix)."""

    def __init__(self, name, child, attempts):
        assert attempts >= 1
        self.name = name
        self.child = child
        self.attempts = attempts


class Par:
    """Run branches concurrently. Branches share one deadline, so the
    wall-time cost is max(branches), never the sum."""

    def __init__(self, name, *branches):
        assert branches, "Par needs at least one branch"
        self.name = name
        self.branches = list(branches)
