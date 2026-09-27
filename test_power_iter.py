"""Self-test / cross-validation harness for power_iter.py.

Standard library only.  Run:  python3 test_power_iter.py

For every case it prints and writes to results.csv:
    eigenvalue relative error vs. reference, eigenvector angle vs.
    reference, iteration count, wall time, final relative residual.

References used:
  * exact known eigenpairs (diagonal / constructed A = Q L Q^T);
  * an independent Jacobi eigenvalue solver (small symmetric matrices);
  * self-consistency across random seeds (large sparse random matrix).
"""

import csv
import math
import random
import sys
import time

from power_iter import SparseMatrix, power_iteration


# ---------------------------------------------------------------------------
# Reference tools
# ---------------------------------------------------------------------------

def jacobi_eig(A, sweeps=100, eps=1e-14):
    """Classic cyclic Jacobi for a small symmetric dense matrix.

    Returns (eigenvalues, eigenvectors-as-columns list)."""
    n = len(A)
    a = [row[:] for row in A]
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(sweeps):
        off = math.sqrt(sum(a[i][j] ** 2 for i in range(n)
                            for j in range(n) if i != j))
        if off < eps:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-300:
                    continue
                theta = 0.5 * math.atan2(2 * a[p][q], a[q][q] - a[p][p])
                c, s = math.cos(theta), math.sin(theta)
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
                for k in range(n):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p] = c * vkp - s * vkq
                    v[k][q] = s * vkp + c * vkq
    return [a[i][i] for i in range(n)], v


def random_orthogonal(n, rng):
    """Random orthogonal matrix via Gram-Schmidt on gaussian columns."""
    cols = [[rng.gauss(0, 1) for _ in range(n)] for _ in range(n)]
    q = []
    for c in cols:
        for u in q:
            d = sum(a * b for a, b in zip(c, u))
            c = [a - d * b for a, b in zip(c, u)]
        nrm = math.sqrt(sum(a * a for a in c))
        q.append([a / nrm for a in c])
    return q  # columns


def qlqt(lambdas, qcols):
    """Build A = Q L Q^T from eigenvalues and orthonormal columns."""
    n = len(lambdas)
    return [[sum(lambdas[k] * qcols[k][i] * qcols[k][j]
                 for k in range(n)) for j in range(n)] for i in range(n)]


def angle_deg(v, ref):
    dot = abs(sum(a * b for a, b in zip(v, ref)))
    nv = math.sqrt(sum(a * a for a in v))
    nr = math.sqrt(sum(a * a for a in ref))
    cos = min(1.0, dot / (nv * nr))
    return math.degrees(math.acos(cos))


def rel_err(a, b):
    return abs(a - b) / max(abs(b), 1e-300)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def build_cases():
    rng = random.Random(42)
    cases = []

    def add(name, A, ref_lam, ref_vec, tol=1e-10, max_iter=100_000,
            eig_tol=1e-8, vec_tol_deg=1e-3, expect_converged=True,
            note="", **kw):
        cases.append(dict(name=name, A=A, ref_lam=ref_lam, ref_vec=ref_vec,
                          tol=tol, max_iter=max_iter, eig_tol=eig_tol,
                          vec_tol_deg=vec_tol_deg,
                          expect_converged=expect_converged, note=note, kw=kw))

    # 1. identity matrix: lambda = 1, every vector is an eigenvector
    add("identity_I50", SparseMatrix.diag([1.0] * 50), 1.0, None,
        vec_tol_deg=None, note="any vector is an eigenvector; angle N/A")

    # 2. zero matrix: lambda = 0
    add("zero_50", SparseMatrix(50), 0.0, None,
        vec_tol_deg=None, note="zero matrix; angle N/A")

    # 3. diagonal matrix, dominant at the end
    d = [float(i + 1) for i in range(100)]
    add("diag_1..100", SparseMatrix.diag(d), 100.0,
        [0.0] * 99 + [1.0])

    # 4. diagonal with negative dominant eigenvalue
    add("diag_negative_dominant", SparseMatrix.diag([-5.0, 2.0, 1.0, 0.3]),
        -5.0, [1.0, 0.0, 0.0, 0.0])

    # 5. extreme scale differences
    add("diag_scale_1e12", SparseMatrix.diag([1e12, 1.0, 1e-12]),
        1e12, [1.0, 0.0, 0.0])
    add("diag_scale_tiny", SparseMatrix.diag([1e-12, 1e-13, 1e-15]),
        1e-12, [1.0, 0.0, 0.0])
    add("diag_scale_mixed", SparseMatrix.diag([1e8, 1e4, 1.0, 1e-8]),
        1e8, [1.0, 0.0, 0.0, 0.0])

    # 6. constructed symmetric A = Q L Q^T, well separated spectrum
    n = 60
    q = random_orthogonal(n, rng)
    lam = [8.0 * (0.5 ** k) for k in range(n)]
    add("constructed_QQLT_gap0.5", SparseMatrix.from_dense(qlqt(lam, q)),
        lam[0], q[0], tol=1e-12, eig_tol=1e-9, vec_tol_deg=1e-4)

    # 7. dominant eigenvalue close to the second one (slow convergence)
    for gap in (1e-2, 1e-3):
        q2 = random_orthogonal(30, rng)
        lam2 = [1.0, 1.0 - gap] + [0.5 * (0.9 ** k) for k in range(28)]
        add(f"near_degenerate_gap{gap:g}",
            SparseMatrix.from_dense(qlqt(lam2, q2)),
            lam2[0], q2[0], tol=1e-8, max_iter=200_000,
            eig_tol=1e-6, vec_tol_deg=1.0,
            note=f"lambda2/lambda1 = {1 - gap:.6f}; linear convergence, "
                 f"rate ~ (1-gap)^2 per iter on the residual")

    # 8. singular matrices
    add("singular_diag", SparseMatrix.diag([3.0, 1.0, 0.0]),
        3.0, [1.0, 0.0, 0.0], note="singular but dominant pair well defined")
    # nilpotent: strictly upper triangular Jordan block, all eigenvalues 0
    nil = SparseMatrix(10)
    for i in range(9):
        nil.rows[i][i + 1] = 1.0
    add("nilpotent_J10", nil, 0.0, None, vec_tol_deg=None,
        note="nilpotent: iteration hits the null space; lambda = 0")

    # 9. initial vector exactly orthogonal to the dominant eigenvector
    A9 = SparseMatrix.diag([3.0, 2.0, 1.0])
    add("orthogonal_start_noverify", A9, 1.0, [0.0, 0.0, 1.0],
        note="x0 = e3 is orthogonal to dominant e1: converges to the "
             "subdominant pair (1, e3); residual = 0, so the residual "
             "criterion alone cannot detect this",
        x0=[0.0, 0.0, 1.0], verify=False)
    add("orthogonal_start_verify", A9, 3.0, [1.0, 0.0, 0.0],
        note="same start, verify pass on: escapes to the dominant pair",
        x0=[0.0, 0.0, 1.0], verify=True)

    # 10. +-lambda degeneracy: residual stalls, must report non-convergence
    add("plus_minus_lambda", SparseMatrix.diag([2.0, -2.0, 1.0]),
        None, None, expect_converged=False, vec_tol_deg=None,
        note="|lambda1| = |lambda2|, opposite signs: no convergence, "
             "clear failure verdict expected")

    # 11. Jacobi cross-check on a random symmetric matrix
    n = 20
    M = [[rng.gauss(0, 1) for _ in range(n)] for _ in range(n)]
    S = [[0.5 * (M[i][j] + M[j][i]) for j in range(n)] for i in range(n)]
    jw, jv = jacobi_eig(S)
    kmax = max(range(n), key=lambda i: abs(jw[i]))
    add("jacobi_crosscheck_n20", SparseMatrix.from_dense(S),
        jw[kmax], [jv[i][kmax] for i in range(n)],
        tol=1e-12, eig_tol=1e-8, vec_tol_deg=1e-3,
        note="reference: independent Jacobi eigensolver")

    # 12. large sparse random symmetric matrix (timing showcase)
    n = 5000
    rows = [dict() for _ in range(n)]
    degree = 10
    for i in range(n):
        rows[i][i] = rng.gauss(0, 1)
        for _ in range(degree):
            j = rng.randrange(n)
            val = rng.gauss(0, 1)
            rows[i][j] = rows[i].get(j, 0.0) + val
            rows[j][i] = rows[j].get(i, 0.0) + val
    add("large_sparse_n5000", SparseMatrix(n, rows), None, None,
        tol=1e-9, max_iter=50_000, vec_tol_deg=None,
        note="no closed-form reference: eigenvalue cross-checked across "
             "two independent random seeds")

    return cases


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    cases = build_cases()
    rows_out = []
    failures = []

    header = (f"{'case':28s} {'n':>5s} {'nnz':>8s} {'conv':>4s} {'iters':>7s} "
              f"{'time_ms':>9s} {'lambda':>14s} {'eig_rel_err':>11s} "
              f"{'ang_deg':>10s} {'rel_resid':>9s}")
    print(header)
    print("-" * len(header))

    for c in cases:
        A = c["A"]
        t0 = time.perf_counter()
        res = power_iteration(A, tol=c["tol"], max_iter=c["max_iter"],
                              seed=1234, logger=None, **c["kw"])
        wall = time.perf_counter() - t0

        eig_err = ""
        ang = ""
        ok = (res.converged == c["expect_converged"])

        if c["ref_lam"] is not None:
            if c["ref_lam"] == 0.0:
                e = abs(res.eigenvalue)
            else:
                e = rel_err(res.eigenvalue, c["ref_lam"])
            eig_err = f"{e:.2e}"
            ok = ok and e <= c["eig_tol"]
        if c["ref_vec"] is not None and c["vec_tol_deg"] is not None:
            a = angle_deg(res.eigenvector, c["ref_vec"])
            ang = f"{a:.2e}"
            ok = ok and a <= c["vec_tol_deg"]

        # extra self-consistency check for the large sparse case
        if c["name"] == "large_sparse_n5000":
            res2 = power_iteration(A, tol=c["tol"], max_iter=c["max_iter"],
                                   seed=99, logger=None)
            e = rel_err(res.eigenvalue, res2.eigenvalue)
            eig_err = f"{e:.2e}(2seeds)"
            ok = ok and e <= 1e-6

        print(f"{c['name']:28s} {A.n:5d} {A.nnz():8d} "
              f"{str(res.converged):>4s} {res.iterations:7d} "
              f"{wall * 1e3:9.1f} {res.eigenvalue:14.6e} "
              f"{eig_err:>11s} {ang:>10s} {res.residual:9.2e}")
        print(f"{'':28s} -> {res.message}")
        if c["note"]:
            print(f"{'':28s}    note: {c['note']}")

        rows_out.append({
            "case": c["name"], "n": A.n, "nnz": A.nnz(),
            "converged": res.converged, "iterations": res.iterations,
            "wall_time_ms": f"{wall * 1e3:.1f}",
            "eigenvalue": f"{res.eigenvalue:.10e}",
            "eig_rel_err": eig_err, "angle_deg": ang,
            "rel_residual": f"{res.residual:.2e}",
            "restarts": res.restarts,
            "verify_escapes": res.verify_escapes,
            "message": res.message, "note": c["note"],
        })
        if not ok:
            failures.append(c["name"])

    with open("results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(rows_out)
    print(f"\nwrote results.csv ({len(rows_out)} cases)")

    if failures:
        print("FAILED cases:", ", ".join(failures))
        return 1
    print("all cases passed their tolerance checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
