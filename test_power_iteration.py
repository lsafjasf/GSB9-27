"""幂迭代自测与对拍（仅标准库）。

参照解来源：
  - 2x2 对称矩阵：特征多项式求根公式（二次方程）；
  - 对角 / 三角矩阵：特征值即对角元（已知构造）；
  - 随机 6-正则稀疏图邻接矩阵：Perron 根 λ1 = 6，特征向量为全 1 向量（已知构造）。

每个用例输出：状态、迭代轮数、耗时、λ、λ_ref、相对误差、特征向量夹角余弦。
判定：|λ-λ_ref|/|λ_ref| <= tol_lam 且 cos(夹角) >= 1 - tol_vec。
"""

import math
import random
import sys

from power_iteration import (to_sparse, dominant_eigenpair, power_iteration,
                             dot, norm)

FAILURES = []


def cos_angle(x, v):
    return abs(dot(x, v)) / (norm(x) * norm(v))


def report(name, n, res, lam_ref=None, v_ref=None, tol_lam=1e-8, tol_vec=1e-8,
           expect_status="converged"):
    rel_err = abs(res["lambda"] - lam_ref) / abs(lam_ref) if lam_ref else float("nan")
    cos = cos_angle(res["vector"], v_ref) if v_ref is not None else float("nan")
    ok = res["status"] == expect_status
    if lam_ref:
        ok = ok and rel_err <= tol_lam
    if v_ref is not None:
        ok = ok and cos >= 1.0 - tol_vec
    print("%-28s n=%-6d %-10s %8d 轮 %9.1f ms  λ=%+.10e" %
          (name, n, res["status"], res["iterations"],
           res["elapsed"] * 1e3, res["lambda"]))
    if lam_ref:
        print("%-28s λ_ref=%+.10e  相对误差=%.2e  cos(夹角)=%.12f  [%s]" %
              ("", lam_ref, rel_err, cos, "PASS" if ok else "FAIL"))
    else:
        print("%-28s [%s]" % ("", "PASS" if ok else "FAIL"))
    for note in res["notes"]:
        print("%-28s 说明: %s" % ("", note))
    if not ok:
        FAILURES.append(name)
    print()


def diag_sparse(entries):
    return [{i: v} for i, v in enumerate(entries)]


def main():
    print("=" * 78)
    print("幂迭代自测与对拍")
    print("=" * 78)

    # 1. 单位矩阵：所有特征值相同，任意向量都是特征向量，应 1 轮收敛
    res = dominant_eigenpair(to_sparse([[1.0 if i == j else 0.0 for j in range(5)]
                                        for i in range(5)]))
    report("单位矩阵 I_5", 5, res, lam_ref=1.0)

    # 2. 零矩阵：A x = 0，应直接给出 λ=0 的结论
    res = dominant_eigenpair(to_sparse([[0.0] * 4 for _ in range(4)]))
    report("零矩阵 0_4", 4, res, lam_ref=None)
    if res["lambda"] != 0.0:
        FAILURES.append("零矩阵 λ!=0")

    # 3. 对角矩阵 diag(5,3,1)：已知构造
    res = dominant_eigenpair(diag_sparse([5.0, 3.0, 1.0]))
    report("对角 diag(5,3,1)", 3, res, lam_ref=5.0, v_ref=[1.0, 0.0, 0.0])

    # 4. 尺度差异极大 diag(1e8, 1, 1e-8)
    res = dominant_eigenpair(diag_sparse([1e8, 1.0, 1e-8]))
    report("尺度差异 diag(1e8,1,1e-8)", 3, res, lam_ref=1e8,
           v_ref=[1.0, 0.0, 0.0])

    # 5. 负主特征值 diag(-5,3,1)
    res = dominant_eigenpair(diag_sparse([-5.0, 3.0, 1.0]))
    report("负主特征值 diag(-5,3,1)", 3, res, lam_ref=-5.0,
           v_ref=[1.0, 0.0, 0.0])

    # 6. 奇异矩阵 diag(4,2,0)：正常收敛到 4
    res = dominant_eigenpair(diag_sparse([4.0, 2.0, 0.0]))
    report("奇异矩阵 diag(4,2,0)", 3, res, lam_ref=4.0, v_ref=[1.0, 0.0, 0.0])

    # 7. 幂零矩阵（移位矩阵）：S^4=0，主特征值 0
    nilp = [{i + 1: 1.0} for i in range(3)] + [{}]
    res = dominant_eigenpair(nilp)
    report("幂零移位矩阵 4x4", 4, res, lam_ref=None)
    if res["lambda"] != 0.0:
        FAILURES.append("幂零矩阵 λ!=0")

    # 8. 主特征值接近次特征值 diag(1, 0.9999, 0.5)：收敛缓慢，应给出提示
    res = dominant_eigenpair(diag_sparse([1.0, 0.9999, 0.5]),
                             tol=1e-8, max_iter=2_000_000)
    report("接近特征值 diag(1,0.9999,0.5)", 3, res, lam_ref=1.0,
           v_ref=[1.0, 0.0, 0.0], tol_lam=1e-6, tol_vec=1e-4)
    if not any("缓慢" in note for note in res["notes"]):
        FAILURES.append("缓慢收敛未检出")

    # 9. λ1 = -λ2：diag(-3,3,1)，幂迭代应明确报不收敛
    res = power_iteration(diag_sparse([-3.0, 3.0, 1.0]), max_iter=3000)
    report("λ1=-λ2: diag(-3,3,1)", 3, res, expect_status="not_converged_oscillating")

    # 10. 初始向量与主特征向量正交：diag(3,2,1), x0=e2
    #     朴素幂迭代会错误地收敛到 λ=2；重启校验应纠正为 λ=3
    naive = power_iteration(diag_sparse([3.0, 2.0, 1.0]), x0=[0.0, 1.0, 0.0])
    print("%-28s 朴素幂迭代（x0=e2）收敛到 λ=%.6f（次特征值，错误结果）" %
          ("正交初始向量-朴素", naive["lambda"]))
    if abs(naive["lambda"] - 2.0) > 1e-9:
        FAILURES.append("正交初始向量复现失败")
    res = dominant_eigenpair(diag_sparse([3.0, 2.0, 1.0]), x0=[0.0, 1.0, 0.0])
    report("正交初始向量-重启校验", 3, res, lam_ref=3.0, v_ref=[1.0, 0.0, 0.0])
    if not any("正交" in note for note in res["notes"]):
        FAILURES.append("正交初始向量未检出")

    # 11. 2x2 对称矩阵对拍：特征多项式 λ²-4λ+3=0 → λ1=3, v=(1,1)/√2
    A = to_sparse([[2.0, 1.0], [1.0, 2.0]])
    tr, det = 4.0, 3.0
    lam_ref = tr / 2.0 + math.sqrt(tr * tr / 4.0 - det)
    res = dominant_eigenpair(A, tol=1e-12)
    report("2x2 特征多项式对拍", 2, res, lam_ref=lam_ref,
           v_ref=[1.0 / math.sqrt(2.0)] * 2, tol_lam=1e-10, tol_vec=1e-10)

    # 12. 上三角矩阵（特征值=对角元 4,3,2,1），主特征向量=e1
    T = to_sparse([[4.0, 1.0, 2.0, 3.0],
                   [0.0, 3.0, 1.0, 2.0],
                   [0.0, 0.0, 2.0, 1.0],
                   [0.0, 0.0, 0.0, 1.0]])
    res = dominant_eigenpair(T, tol=1e-12)
    report("上三角 4x4 已知构造", 4, res, lam_ref=4.0,
           v_ref=[1.0, 0.0, 0.0, 0.0], tol_lam=1e-10, tol_vec=1e-10)

    # 13. 大规模稀疏：n=10000 随机 6-正则图邻接矩阵（环 + 2 个随机置换）
    #     每行和恰为 6 → λ1=6（Perron 根），v1=全 1 向量/√n
    n = 10000
    rng = random.Random(42)
    rows = [dict() for _ in range(n)]

    def add_edge(i, j):
        rows[i][j] = rows[i].get(j, 0.0) + 1.0
        rows[j][i] = rows[j].get(i, 0.0) + 1.0

    for i in range(n):
        add_edge(i, (i + 1) % n)
    for _ in range(2):
        perm = list(range(n))
        rng.shuffle(perm)
        for i, j in enumerate(perm):
            add_edge(i, j)
    v_ref = [1.0 / math.sqrt(n)] * n
    res = dominant_eigenpair(rows, tol=1e-9, max_iter=20000, log_every=50)
    report("随机6-正则稀疏图 n=10000", n, res, lam_ref=6.0, v_ref=v_ref,
           tol_lam=1e-8, tol_vec=1e-6)

    print("=" * 78)
    if FAILURES:
        print("失败用例: %s" % ", ".join(FAILURES))
        return 1
    print("全部用例通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
