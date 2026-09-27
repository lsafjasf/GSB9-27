"""mcint: Monte Carlo integration library (Python 3, standard library only).

Estimators
----------
- crude_mc            : plain uniform sampling over a hyper-rectangle.
- stratified_mc       : stratified sampling on a regular grid of strata.
- importance_sampling : sample from a user-supplied proposal density.
- control_variate     : use a correlated function with known integral.

All estimators return an MCResult with the integral estimate, its standard
error, the number of integrand evaluations and the wall-clock time.

The random source is injectable: pass any object with the `random.Random`
interface (`uniform`, `gauss`, ...). A `random.Random(seed)` instance makes
every result fully reproducible.
"""

import math
import random
import time
from typing import Callable, Optional, Sequence

__all__ = [
    "MCResult",
    "crude_mc",
    "stratified_mc",
    "importance_sampling",
    "control_variate",
]


class MCResult:
    """Container for an integration result."""

    __slots__ = ("estimate", "stderr", "n", "elapsed")

    def __init__(self, estimate: float, stderr: float, n: int, elapsed: float):
        self.estimate = estimate
        self.stderr = stderr
        self.n = n                # number of integrand evaluations actually used
        self.elapsed = elapsed    # wall-clock seconds

    def ci(self, z: float = 1.959964):
        """Symmetric confidence interval (z=1.96 -> ~95%)."""
        return (self.estimate - z * self.stderr, self.estimate + z * self.stderr)

    def __repr__(self):
        return (f"MCResult(estimate={self.estimate:.8g}, stderr={self.stderr:.3g}, "
                f"n={self.n}, elapsed={self.elapsed:.3f}s)")


def _mean_stderr(values):
    n = len(values)
    mean = math.fsum(values) / n
    if n < 2:
        return mean, float("nan")
    var = math.fsum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(var / n)


def _check_rng(rng):
    return rng if rng is not None else random.Random()


def crude_mc(f: Callable[[list], float],
             dim: int,
             n: int,
             rng: Optional[random.Random] = None,
             lower: Optional[Sequence[float]] = None,
             upper: Optional[Sequence[float]] = None) -> MCResult:
    """Plain Monte Carlo over the box [lower, upper] (default [0,1]^dim).

    estimate = V * mean(f(x_i)),  x_i ~ Uniform(box),  V = box volume.
    """
    rng = _check_rng(rng)
    lo = list(lower) if lower is not None else [0.0] * dim
    hi = list(upper) if upper is not None else [1.0] * dim
    vol = math.prod(h - l for l, h in zip(lo, hi))
    t0 = time.perf_counter()
    values = [f([rng.uniform(l, h) for l, h in zip(lo, hi)]) for _ in range(n)]
    mean, se = _mean_stderr(values)
    return MCResult(vol * mean, vol * se, n, time.perf_counter() - t0)


def _strata_cells(strata: Sequence[int]):
    """Yield (index_tuple, lo_list, hi_list) for every cell of the grid."""
    dim = len(strata)
    idx = [0] * dim
    while True:
        yield tuple(idx)
        for k in range(dim - 1, -1, -1):
            idx[k] += 1
            if idx[k] < strata[k]:
                break
            idx[k] = 0
        else:
            return


def stratified_mc(f: Callable[[list], float],
                  dim: int,
                  n: int,
                  rng: Optional[random.Random] = None,
                  strata=4,
                  lower: Optional[Sequence[float]] = None,
                  upper: Optional[Sequence[float]] = None) -> MCResult:
    """Stratified sampling on a regular grid.

    `strata` is an int (same number of strata per axis) or a sequence of
    per-axis stratum counts. Samples are allocated proportionally to cell
    volume (uniform allocation for a regular grid); every cell gets at
    least one sample. The estimator is the volume-weighted sum of the
    per-stratum means, and its variance is the sum of the per-stratum
    variances (strata are independent).
    """
    rng = _check_rng(rng)
    lo = list(lower) if lower is not None else [0.0] * dim
    hi = list(upper) if upper is not None else [1.0] * dim
    if isinstance(strata, int):
        strata = [strata] * dim
    strata = list(strata)
    n_cells = math.prod(strata)
    if n < n_cells:
        raise ValueError(f"n={n} < number of strata cells={n_cells}")
    per_cell = n // n_cells
    # distribute the remainder over the first cells
    rem = n - per_cell * n_cells
    vol = math.prod(h - l for l, h in zip(lo, hi))
    cell_vol = vol / n_cells

    t0 = time.perf_counter()
    estimate = 0.0
    variance = 0.0
    used = 0
    for cell_i, idx in enumerate(_strata_cells(strata)):
        m = per_cell + (1 if cell_i < rem else 0)
        clo = [l + (h - l) * i / s for i, (l, h, s) in zip(idx, zip(lo, hi, strata))]
        chi = [l + (h - l) * (i + 1) / s for i, (l, h, s) in zip(idx, zip(lo, hi, strata))]
        vals = [f([rng.uniform(a, b) for a, b in zip(clo, chi)]) for _ in range(m)]
        mean, se = _mean_stderr(vals)
        estimate += cell_vol * mean
        if m > 1:
            variance += (cell_vol ** 2) * se * se  # se^2 = var of cell mean
        used += m
    return MCResult(estimate, math.sqrt(variance), used, time.perf_counter() - t0)


def importance_sampling(f: Callable[[list], float],
                        dim: int,
                        n: int,
                        rng: Optional[random.Random] = None,
                        sample_proposal: Optional[Callable[[random.Random], list]] = None,
                        proposal_pdf: Optional[Callable[[list], float]] = None) -> MCResult:
    """Importance sampling.

    Draw x_i ~ q (via `sample_proposal(rng)`), then
        estimate = mean( f(x_i) / q(x_i) )
    where q = `proposal_pdf` must be a *normalised* density with support
    covering {f != 0}. The integral is over the whole support of q.
    """
    rng = _check_rng(rng)
    if sample_proposal is None or proposal_pdf is None:
        raise ValueError("sample_proposal and proposal_pdf are required")
    t0 = time.perf_counter()
    values = []
    for _ in range(n):
        x = sample_proposal(rng)
        q = proposal_pdf(x)
        values.append(f(x) / q if q > 0.0 else 0.0)
    mean, se = _mean_stderr(values)
    return MCResult(mean, se, n, time.perf_counter() - t0)


def control_variate(f: Callable[[list], float],
                    g: Callable[[list], float],
                    g_integral: float,
                    dim: int,
                    n: int,
                    rng: Optional[random.Random] = None,
                    lower: Optional[Sequence[float]] = None,
                    upper: Optional[Sequence[float]] = None) -> MCResult:
    """Control-variate estimator over the box [lower, upper].

    Uses g with known integral `g_integral` over the box:
        estimate = mean( f(x_i) - c*(g(x_i) - E[g]) ),  E[g] = g_integral / V
    The optimal coefficient c = Cov(f,g)/Var(g) is estimated from the same
    samples (standard practice; the induced bias is O(1/n)).
    """
    rng = _check_rng(rng)
    lo = list(lower) if lower is not None else [0.0] * dim
    hi = list(upper) if upper is not None else [1.0] * dim
    vol = math.prod(h - l for l, h in zip(lo, hi))
    g_mean = g_integral / vol
    t0 = time.perf_counter()
    fs, gs = [], []
    for _ in range(n):
        x = [rng.uniform(l, h) for l, h in zip(lo, hi)]
        fs.append(f(x))
        gs.append(g(x))
    fm = math.fsum(fs) / n
    gm = math.fsum(gs) / n
    cov = math.fsum((a - fm) * (b - gm) for a, b in zip(fs, gs)) / (n - 1)
    varg = math.fsum((b - gm) ** 2 for b in gs) / (n - 1)
    c = cov / varg if varg > 0 else 0.0
    adjusted = [fv - c * (gv - g_mean) for fv, gv in zip(fs, gs)]
    mean, se = _mean_stderr(adjusted)
    return MCResult(vol * mean, vol * se, n, time.perf_counter() - t0)
