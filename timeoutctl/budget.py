"""The shared timeout budget.

A Budget is just an absolute deadline on the injected clock. It is created
once at the top of a pipeline and the *same object* is handed down to every
child step, every retry attempt and every parallel branch, so nobody can
reset it. `remaining()` only ever shrinks as the clock advances.
"""


class Budget:
    __slots__ = ("deadline",)

    def __init__(self, deadline):
        self.deadline = deadline

    @classmethod
    def fresh(cls, clock, seconds):
        return cls(clock.now() + seconds)

    def remaining(self, clock):
        return max(0.0, self.deadline - clock.now())

    def exhausted(self, clock):
        return self.remaining(clock) <= 0.0

    def limited(self, clock, seconds):
        """Optional tighter cap for a sub-step. The derived budget can never
        exceed the parent's deadline, so the global bound still holds."""
        return Budget(min(self.deadline, clock.now() + seconds))
