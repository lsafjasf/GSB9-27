"""Power iteration for the dominant eigenpair of large sparse matrices.

Pure Python 3 (standard library only).

Features
--------
- Sparse matrix stored as row dicts; O(nnz) matvec.
- Rayleigh-quotient eigenvalue estimate each iteration.
- Convergence via *relative residual*  ||A v - lambda v|| / |lambda|,
  never a fixed iteration count.
- Explicit handling of degenerate cases:
    * dominant eigenvalue close to the second one (slow linear convergence,
      detected and reported via the observed contraction rate);
    * singular / zero / nilpotent matrices (null-space detection, lambda = 0);
    * initial vector (numerically) orthogonal to the dominant eigenvector
      (post-convergence verification pass with a perturbed restart);
    * eigenvalues of equal magnitude but opposite sign (+-lambda): the
      residual stalls, stagnation is detected and reported as non-convergence.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Sparse matrix
# ---------------------------------------------------------------------------

class SparseMatrix:
    """Row-major sparse matrix: ``rows[i]`` maps column index -> value."""

    def __init__(self, n: int, rows: Optional[list] = None):
        self.n = n
        self.rows = rows if rows is not None else [dict() for _ in range(n)]

    @classmethod
    def from_dense(cls, dense, drop_tol: float = 0.0) -> "SparseMatrix":
        n = len(dense)
        rows = []
        for row in dense:
            if len(row) != n:
                raise ValueError("dense matrix must be square")
            rows.append({j: v for j, v in enumerate(row) if abs(v) > drop_tol})
        return cls(n, rows)

    @classmethod
    def diag(cls, values) -> "SparseMatrix":
        return cls(len(values), [{i: float(v)} for i, v in enumerate(values)])

    def matvec(self, x):
        return [sum(v * x[j] for j, v in row.items()) for row in self.rows]

    def nnz(self) -> int:
        return sum(len(r) for r in self.rows)

    def frobenius_norm(self) -> float:
        return math.sqrt(sum(v * v for row in self.rows for v in row.values()))


# ---------------------------------------------------------------------------
# Small vector helpers
# ---------------------------------------------------------------------------

def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(a) -> float:
    return math.sqrt(sum(x * x for x in a))


def _normalize(a):
    nrm = _norm(a)
    if nrm == 0.0:
        raise ValueError("cannot normalize the zero vector")
    return [x / nrm for x in a]


def _random_unit(n: int, rng: random.Random):
    return _normalize([rng.gauss(0.0, 1.0) for _ in range(n)])


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class PowerIterationResult:
    eigenvalue: float
    eigenvector: list
    converged: bool
    iterations: int                 # total matvecs spent (incl. restarts/verify)
    residual: float                 # final relative residual
    history: list = field(default_factory=list)   # (k, lambda_k, rel_res_k)
    restarts: int = 0
    verify_escapes: int = 0
    wall_time: float = 0.0
    message: str = ""


# ---------------------------------------------------------------------------
# Power iteration
# ---------------------------------------------------------------------------

def power_iteration(
    A: SparseMatrix,
    x0=None,
    tol: float = 1e-10,
    max_iter: int = 100_000,
    seed: int = 0,
    verify: bool = True,
    verify_iters: int = 200,
    max_restarts: int = 3,
    stall_window: int = 2_000,
    stall_factor: float = 0.9,
    logger: Optional[Callable[[int, float, float], None]] = print,
) -> PowerIterationResult:
    """Compute the dominant (largest-|value|) eigenpair of ``A``.

    Convergence criterion (checked every iteration):
        rel_res = ||A v - lambda v||_2 / max(|lambda|, tiny) <= tol
    with ``lambda = v^T A v`` the Rayleigh quotient and ``||v|| = 1``.
    """
    rng = random.Random(seed)
    n = A.n
    a_fro = A.frobenius_norm()
    tiny = 1e-300

    history = []
    restarts = 0
    escapes = 0
    total_iter = 0      # budget counter: every matvec counts
    main_iter = 0       # iterations of the main power loop only
    t_start = time.perf_counter()

    v = _normalize(list(x0)) if x0 is not None else _random_unit(n, rng)
    lam = 0.0
    res = math.inf
    converged = False
    message = "maximum iterations reached without convergence"

    def log(k, la, rs):
        history.append((k, la, rs))
        if logger is not None:
            logger(k, la, rs)

    while True:
        window_res = None
        null_hits = 0

        while total_iter < max_iter:
            w = A.matvec(v)
            nw = _norm(w)
            total_iter += 1
            main_iter += 1

            if nw <= tiny:
                # v lies (numerically) in the null space of A.
                lam, res = 0.0, 0.0
                log(total_iter, lam, res)
                if a_fro == 0.0:
                    converged = True
                    message = ("zero matrix: every vector is an eigenvector "
                               "with eigenvalue 0")
                    break
                null_hits += 1
                if null_hits <= max_restarts:
                    # Random restart: maybe only this start was in the null space.
                    restarts += 1
                    v = _random_unit(n, rng)
                    continue
                converged = True
                message = ("repeated random starts all landed in the null "
                           "space: the matrix appears nilpotent / zero-dominant; "
                           "dominant eigenvalue is 0 (returned eigenvector is "
                           "a genuine null vector)")
                break

            lam = _dot(v, w)                    # Rayleigh quotient, ||v|| = 1
            res = _norm([wi - lam * vi for wi, vi in zip(w, v)]) \
                / max(abs(lam), tiny)
            log(total_iter, lam, res)
            v = [wi / nw for wi in w]

            if res <= tol:
                converged = True
                if escapes:
                    message = ("converged after the verify pass escaped a "
                               "subdominant eigenpair (initial vector was "
                               "orthogonal to the dominant eigenvector)")
                else:
                    message = "converged: relative residual below tolerance"
                break

            # Stagnation check: residual not contracting over a window.
            if total_iter % stall_window == 0:
                if window_res is not None and res > stall_factor * window_res:
                    restarts += 1
                    if restarts > max_restarts:
                        converged = False
                        message = (
                            "residual stagnated across random restarts; "
                            "likely eigenvalues of (nearly) equal magnitude "
                            "with opposite signs or a tight cluster at the "
                            "top of the spectrum -- plain power iteration "
                            "cannot separate them")
                        break
                    v = _random_unit(n, rng)
                    window_res = None
                    continue
                window_res = res

        if not converged:
            break
        if total_iter >= max_iter and res > tol:
            converged = False
            break

        # Verification pass: a converged pair can still be *subdominant* if
        # x0 was (numerically) orthogonal to the true dominant eigenvector.
        # Perturb and iterate briefly; escape if a larger |lambda| shows up.
        if not verify or a_fro == 0.0 or lam == 0.0 or escapes > max_restarts:
            break
        probe = _normalize([vi + 0.5 * g for vi, g in
                            zip(v, [rng.gauss(0.0, 1.0) for _ in range(n)])])
        best_lam, best_v = lam, None
        for _ in range(verify_iters):
            w = A.matvec(probe)
            nw = _norm(w)
            total_iter += 1
            if nw <= tiny:
                break
            l2 = _dot(probe, w)
            probe = [wi / nw for wi in w]
            if abs(l2) > abs(best_lam):
                best_lam, best_v = l2, probe
        if best_v is not None and abs(best_lam) > abs(lam) * (1.0 + 1e-6):
            escapes += 1
            restarts += 1
            v = best_v
            continue
        break

    wall = time.perf_counter() - t_start
    return PowerIterationResult(
        eigenvalue=lam,
        eigenvector=v,
        converged=converged,
        iterations=main_iter,
        residual=res,
        history=history,
        restarts=restarts,
        verify_escapes=escapes,
        wall_time=wall,
        message=message,
    )


# ---------------------------------------------------------------------------
# Verbose demo:  python3 power_iter.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    def _default_logger(k, la, rs):
        print(f"iter {k:5d} | lambda ~= {la:+.12e} | rel_residual = {rs:.3e}")

    demo = SparseMatrix.diag([4.0, 2.0, 1.0, 0.5])
    print("demo: diag(4, 2, 1, 0.5), tol = 1e-12")
    result = power_iteration(demo, tol=1e-12, seed=1, logger=_default_logger)
    print(f"-> lambda = {result.eigenvalue:.15e}, "
          f"converged = {result.converged}, "
          f"iters = {result.iterations}, "
          f"residual = {result.residual:.2e}")
    print(f"-> {result.message}")
