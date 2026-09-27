"""Adaptive-step embedded Runge-Kutta 4(5) ODE solver (Python stdlib only).

Method: Dormand-Prince 5(4) pair. The *fourth-order* formula is propagated
and the fifth-order formula supplies the local error estimate, i.e. a true
fourth-order method with adaptive step-size control (RKF45-style usage).

Public API
----------
integrate(f, t0, y0, t1, ...) -> Solution
    Solve y' = f(t, y) from t0 to t1 (t1 < t0 is allowed: backward
    integration). `y0` may be a float or a sequence of floats.

IntegrationFailure
    Raised when the step size would have to drop below `h_min` to satisfy
    the tolerance. Carries the location `t` and state `y` at the failure
    point so the caller can see exactly where integration got stuck.

Every attempted step (accepted or rejected) is recorded in
`Solution.history` as a StepRecord(t, h, err_norm, accepted), so the full
step-size / error-estimate sequence is available for inspection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, List, Sequence, Union

Number = Union[int, float]
Vector = List[float]

# ---------------------------------------------------------------------------
# Dormand-Prince 5(4) coefficients
# ---------------------------------------------------------------------------
_A21 = 1.0 / 5.0
_A31, _A32 = 3.0 / 40.0, 9.0 / 40.0
_A41, _A42, _A43 = 44.0 / 45.0, -56.0 / 15.0, 32.0 / 9.0
_A51, _A52, _A53, _A54 = (
    19372.0 / 6561.0,
    -25360.0 / 2187.0,
    64448.0 / 6561.0,
    -212.0 / 729.0,
)
_A61, _A62, _A63, _A64, _A65 = (
    9017.0 / 3168.0,
    -355.0 / 33.0,
    46732.0 / 5247.0,
    49.0 / 176.0,
    -5103.0 / 18656.0,
)
# 5th-order weights
_B51, _B53, _B54, _B55, _B56 = (
    35.0 / 384.0,
    500.0 / 1113.0,
    125.0 / 192.0,
    -2187.0 / 6784.0,
    11.0 / 84.0,
)
# 4th-order weights (the propagated solution)
_B41, _B43, _B44, _B45, _B46, _B47 = (
    5179.0 / 57600.0,
    7571.0 / 16695.0,
    393.0 / 640.0,
    -92097.0 / 339200.0,
    187.0 / 2100.0,
    1.0 / 40.0,
)

# Step-size controller parameters
_SAFETY = 0.9
_FAC_MIN = 0.2
_FAC_MAX = 10.0
_ERR_EXPONENT = -1.0 / 5.0  # local error of the 4th-order formula is O(h^5)


class IntegrationFailure(RuntimeError):
    """Raised when the tolerance cannot be met without h < h_min."""

    def __init__(self, message: str, t: float, y: Vector, h: float):
        super().__init__(message)
        self.t = t
        self.y = list(y)
        self.h = h


@dataclass
class StepRecord:
    """One attempted step."""

    index: int
    t: float          # start of the attempted step
    h: float          # signed step size attempted
    err_norm: float   # scaled local error estimate (<= 1 means acceptable)
    accepted: bool


@dataclass
class Solution:
    """Result of a successful integration."""

    t: List[float] = field(default_factory=list)
    y: List[Vector] = field(default_factory=list)
    history: List[StepRecord] = field(default_factory=list)
    nfev: int = 0

    @property
    def n_accepted(self) -> int:
        return len(self.t) - 1

    @property
    def n_rejected(self) -> int:
        return sum(1 for r in self.history if not r.accepted)


def _as_vector(y0: Union[Number, Sequence[Number]]) -> tuple[Vector, bool]:
    if isinstance(y0, (int, float)):
        return [float(y0)], True
    return [float(v) for v in y0], False


def _rk45_step(
    f: Callable[[float, Vector], Sequence[float]],
    t: float,
    y: Vector,
    h: float,
) -> tuple[Vector, Vector]:
    """One Dormand-Prince step. Returns (y4, err) where err = y5 - y4."""
    k1 = list(f(t, y))
    k2 = list(f(t + h / 5.0, [yi + h * (_A21 * a) for yi, a in zip(y, k1)]))
    k3 = list(f(
        t + 3.0 * h / 10.0,
        [yi + h * (_A31 * a + _A32 * b) for yi, a, b in zip(y, k1, k2)],
    ))
    k4 = list(f(
        t + 4.0 * h / 5.0,
        [yi + h * (_A41 * a + _A42 * b + _A43 * c)
         for yi, a, b, c in zip(y, k1, k2, k3)],
    ))
    k5 = list(f(
        t + 8.0 * h / 9.0,
        [yi + h * (_A51 * a + _A52 * b + _A53 * c + _A54 * d)
         for yi, a, b, c, d in zip(y, k1, k2, k3, k4)],
    ))
    k6 = list(f(
        t + h,
        [yi + h * (_A61 * a + _A62 * b + _A63 * c + _A64 * d + _A65 * e)
         for yi, a, b, c, d, e in zip(y, k1, k2, k3, k4, k5)],
    ))
    y5 = [yi + h * (_B51 * a + _B53 * c + _B54 * d + _B55 * e + _B56 * g)
          for yi, a, c, d, e, g in zip(y, k1, k3, k4, k5, k6)]
    k7 = list(f(t + h, y5))
    y4 = [yi + h * (_B41 * a + _B43 * c + _B44 * d + _B45 * e + _B46 * g
                    + _B47 * l)
          for yi, a, c, d, e, g, l in zip(y, k1, k3, k4, k5, k6, k7)]
    err = [b - a for a, b in zip(y4, y5)]
    return y4, err


def _error_norm(err: Vector, y_old: Vector, y_new: Vector,
                rtol: float, atol: float) -> float:
    """RMS norm of err scaled by atol + rtol * |y| (<= 1 means acceptable)."""
    acc = 0.0
    for e, yo, yn in zip(err, y_old, y_new):
        scale = atol + rtol * max(abs(yo), abs(yn))
        acc += (e / scale) ** 2
    return math.sqrt(acc / len(err))


def integrate(
    f: Callable[[float, Vector], Union[Number, Sequence[Number]]],
    t0: float,
    y0: Union[Number, Sequence[Number]],
    t1: float,
    *,
    rtol: float = 1e-6,
    atol: float = 1e-9,
    h0: float | None = None,
    h_min: float = 1e-12,
    h_max: float = math.inf,
    max_steps: int = 100_000,
) -> Solution:
    """Integrate y' = f(t, y) from t0 to t1 with adaptive step control.

    `f` receives (t, y_list) and may return a float (scalar problem) or a
    sequence. Raises IntegrationFailure if the tolerance cannot be met with
    h >= h_min; the exception reports the failure location.
    """
    if t1 == t0:
        raise ValueError("empty integration interval")
    if h_min <= 0 or h_max <= 0:
        raise ValueError("h_min and h_max must be positive")
    if h_min > h_max:
        raise ValueError("h_min must not exceed h_max")

    def rhs(t: float, y: Vector) -> Vector:
        out = f(t, list(y))
        if isinstance(out, (int, float)):
            return [float(out)]
        return [float(v) for v in out]

    y, scalar = _as_vector(y0)
    direction = 1.0 if t1 > t0 else -1.0
    span = abs(t1 - t0)

    if h0 is None:
        h_abs = min(span * 1e-2, h_max)
    else:
        h_abs = min(abs(h0), h_max)
    h_abs = max(h_abs, h_min)
    h = direction * h_abs

    t = t0
    sol = Solution(t=[t0], y=[list(y)])
    nfev = 0
    index = 0

    while (t1 - t) * direction > 0.0:
        if index >= max_steps:
            raise IntegrationFailure(
                f"max_steps={max_steps} exceeded at t={t!r}", t, y, h)
        # Do not step past the end of the interval.
        if (t + h - t1) * direction > 0.0:
            h = t1 - t

        y_new, err = _rk45_step(rhs, t, y, h)
        nfev += 7
        err_norm = _error_norm(err, y, y_new, rtol, atol)

        if err_norm <= 1.0:
            # Step accepted.
            sol.history.append(StepRecord(index, t, h, err_norm, True))
            t = t + h
            y = y_new
            sol.t.append(t)
            sol.y.append(list(y))
            if err_norm == 0.0:
                factor = _FAC_MAX
            else:
                factor = min(_FAC_MAX, max(
                    _FAC_MIN, _SAFETY * err_norm ** _ERR_EXPONENT))
            h_abs = min(abs(h) * factor, h_max)
            h_abs = max(h_abs, h_min)
            h = direction * h_abs
        else:
            # Step rejected: shrink. Refuse to spin below h_min.
            factor = max(_FAC_MIN, _SAFETY * err_norm ** _ERR_EXPONENT)
            h_new_abs = abs(h) * factor
            if abs(h) <= h_min or h_new_abs < h_min:
                sol.nfev = nfev
                raise IntegrationFailure(
                    "tolerance not met at minimum step size: "
                    f"t={t!r}, |h|={abs(h):.3e} <= h_min={h_min:.1e}, "
                    f"scaled error estimate={err_norm:.3e} > 1",
                    t, y, h)
            sol.history.append(StepRecord(index, t, h, err_norm, False))
            h = direction * h_new_abs
        index += 1

    sol.nfev = nfev
    if scalar:
        sol.y = [[row[0]] for row in sol.y]
    return sol
