"""Adaptive-step 4th-order Runge-Kutta IVP solver (RKF45), stdlib only.

The solution is advanced with the 4th-order Fehlberg formula and the
local error is estimated from the embedded 5th-order formula.  The step
size is adapted so the normalized local error stays below 1, where each
component is scaled by ``atol + rtol * |y|``.

Every attempted step (accepted or rejected) is recorded with its step
size and error estimate.  If the step size would have to drop below
``h_min`` to satisfy the tolerance, the solver stops and reports failure
together with the location, instead of spinning with too-small steps.
"""

from collections import namedtuple

__all__ = ["Step", "Solution", "solve"]

# One attempted step.  ``err`` is the normalized local error estimate
# (max over components of |err_i| / (atol + rtol*|y_i|)); values <= 1 are
# accepted.  ``t`` is the time at the *start* of the step.
Step = namedtuple("Step", ["index", "t", "h", "err", "accepted"])

# Fehlberg RKF45 coefficients.
_C = (1.0 / 4.0, 3.0 / 8.0, 12.0 / 13.0, 1.0, 1.0 / 2.0)
_B = (
    (1.0 / 4.0,),
    (3.0 / 32.0, 9.0 / 32.0),
    (1932.0 / 2197.0, -7200.0 / 2197.0, 7296.0 / 2197.0),
    (439.0 / 216.0, -8.0, 3680.0 / 513.0, -845.0 / 4104.0),
    (-8.0 / 27.0, 2.0, -3544.0 / 2565.0, 1859.0 / 4104.0, -11.0 / 40.0),
)
_W4 = (25.0 / 216.0, 0.0, 1408.0 / 2565.0, 2197.0 / 4104.0, -1.0 / 5.0, 0.0)
_W5 = (16.0 / 135.0, 0.0, 6656.0 / 12825.0, 28561.0 / 56430.0, -9.0 / 50.0, 2.0 / 55.0)
_E = tuple(w5 - w4 for w4, w5 in zip(_W4, _W5))

_SAFETY = 0.9
_MIN_FACTOR = 0.1
_MAX_FACTOR = 5.0


class Solution:
    """Result of :func:`solve`.

    Attributes:
        t: accepted time points (starts at t0, ends at the reached point).
        y: accepted state values, aligned with ``t``.
        steps: every attempted step as a :class:`Step` record.
        nfev: number of right-hand-side evaluations.
        status: ``"success"`` or ``"failed"``.
        message: human-readable description, includes the failure location.
    """

    def __init__(self):
        self.t = []
        self.y = []
        self.steps = []
        self.nfev = 0
        self.status = "success"
        self.message = "converged"

    @property
    def ok(self):
        return self.status == "success"

    @property
    def accepted_steps(self):
        return [s for s in self.steps if s.accepted]

    @property
    def rejected_steps(self):
        return [s for s in self.steps if not s.accepted]


def _rkf45_step(fw, t, y, h):
    """One RKF45 step from (t, y); returns (y_new, err) component lists."""
    n = len(y)
    k = [fw(t, y)]
    for i in range(5):
        coeffs = _B[i]
        yi = []
        for j in range(n):
            s = 0.0
            for m, b in enumerate(coeffs):
                s += b * k[m][j]
            yi.append(y[j] + h * s)
        k.append(fw(t + _C[i] * h, yi))
    y_new = [y[j] + h * sum(_W4[m] * k[m][j] for m in range(6)) for j in range(n)]
    err = [h * sum(_E[m] * k[m][j] for m in range(6)) for j in range(n)]
    return y_new, err


def solve(f, t0, t1, y0, atol=1e-8, rtol=1e-6, h0=None,
          h_min=1e-12, h_max=None, max_steps=100000):
    """Solve y' = f(t, y) from t0 to t1 with adaptive RKF45.

    Args:
        f: right-hand side; takes (t, y) and returns dy/dt.  ``y`` is a
            float if ``y0`` is scalar, otherwise a list of floats.
        t0, t1: integration bounds; t1 < t0 integrates backwards.
        y0: initial state, scalar float or sequence of floats.
        atol, rtol: absolute/relative tolerances for the local error.
        h0: initial step size (default: |t1 - t0| / 100).
        h_min: minimum allowed |h|; failing to meet the tolerance with
            |h| <= h_min aborts the integration with status "failed".
        h_max: maximum allowed |h| (default: |t1 - t0| / 10).
        max_steps: maximum number of attempted steps before giving up.

    Returns:
        A :class:`Solution`.  Check ``sol.ok`` / ``sol.status`` before
        using ``sol.y[-1]``.
    """
    if h_min <= 0:
        raise ValueError("h_min must be positive")
    scalar = isinstance(y0, (int, float))
    y = [float(y0)] if scalar else [float(v) for v in y0]

    def fw(t, y_list):
        out = f(t, y_list[0] if scalar else list(y_list))
        return [float(out)] if scalar else [float(v) for v in out]

    sol = Solution()
    sol.t.append(float(t0))
    sol.y.append(y[0] if scalar else list(y))

    span = float(t1) - float(t0)
    if span == 0.0:
        sol.message = "empty interval"
        return sol
    direction = 1.0 if span > 0 else -1.0
    if h_max is None:
        h_max = abs(span) / 10.0
    if h0 is None:
        h0 = abs(span) / 100.0
    h = direction * min(abs(h0), h_max)

    t = float(t0)
    n = len(y)
    index = 0

    def fail(message):
        sol.status = "failed"
        sol.message = message
        return sol

    while (t1 - t) * direction > 0.0:
        if index >= max_steps:
            return fail(
                "exceeded max_steps=%d at t = %.12g" % (max_steps, t))
        # Land exactly on the endpoint with the last step.
        if (t + h - t1) * direction > 0.0:
            h = t1 - t
        if abs(h) < h_min:
            return fail(
                "step size |h| = %.3e below h_min = %.3e at t = %.12g; "
                "cannot satisfy tolerance" % (abs(h), h_min, t))

        y_new, err = _rkf45_step(fw, t, y, h)
        sol.nfev += 6
        err_norm = 0.0
        for j in range(n):
            scale = atol + rtol * max(abs(y[j]), abs(y_new[j]))
            err_norm = max(err_norm, abs(err[j]) / scale)

        accepted = err_norm <= 1.0
        sol.steps.append(Step(index, t, h, err_norm, accepted))
        index += 1

        if err_norm == 0.0:
            factor = _MAX_FACTOR
        else:
            factor = _SAFETY * err_norm ** (-0.2)
            factor = min(_MAX_FACTOR, max(_MIN_FACTOR, factor))
        h_next = h * factor

        if not accepted and abs(h_next) < h_min:
            return fail(
                "minimum step size reached at t = %.12g: local error "
                "estimate %.3e exceeds tolerance but |h| = %.3e would "
                "drop below h_min = %.3e" % (t, err_norm, abs(h_next), h_min))

        if accepted:
            t += h
            y = y_new
            sol.t.append(t)
            sol.y.append(y[0] if scalar else list(y))

        h = direction * min(abs(h_next), h_max)

    sol.message = "converged in %d accepted / %d attempted steps" % (
        len(sol.accepted_steps), len(sol.steps))
    return sol
