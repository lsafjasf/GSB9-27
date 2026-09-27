"""幂迭代法求大规模稀疏矩阵的主特征值与主特征向量（仅 Python 标准库）。

矩阵表示（二选一）：
  - 稀疏行字典：list[dict[int, float]]，A[i] = {j: a_ij, ...}，零元不存储；
  - 可调用对象：f(x) -> A @ x（用于结构矩阵，如 对角 + 低秩）。

收敛判据：相对残差  ||A x - λ x||₂ / |λ|  <  tol（λ 为 Rayleigh 商），
不使用固定迭代轮数。λ 接近 0 时退化为绝对残差（见代码注释）。

退化情形处理：
  1. 零矩阵 / 幂零方向：A x = 0，直接返回 λ = 0 并说明；
  2. λ1 ≈ -λ2（幅值相同、符号相反）：特征值近似振荡，检测后明确报"不收敛"；
  3. |λ1| ≈ |λ2|（同号接近）：收敛极慢，估计收缩率 q ≈ |λ2/λ1| 并提示；
  4. 初始向量与主特征向量（近似）正交：会收敛到次特征对，
     dominant_eigenpair() 通过一次随机重启校验并自动纠正。
"""

import math
import random
import time

__all__ = ["to_sparse", "matvec", "power_iteration", "dominant_eigenpair"]


def to_sparse(dense):
    """稠密 list[list[float]] 转稀疏行字典表示。"""
    return [{j: v for j, v in enumerate(row) if v != 0.0} for row in dense]


def matvec(A, x):
    """计算 y = A x。A 为稀疏行字典或可调用对象。"""
    if callable(A):
        return A(x)
    y = [0.0] * len(A)
    for i, row in enumerate(A):
        s = 0.0
        for j, v in row.items():
            s += v * x[j]
        y[i] = s
    return y


def dot(a, b):
    return math.fsum(ai * bi for ai, bi in zip(a, b))


def norm(a):
    return math.sqrt(math.fsum(ai * ai for ai in a))


def _analyze_history(history):
    """根据迭代历史识别退化模式，返回说明文字列表。"""
    notes = []
    if len(history) < 60:
        return notes
    # 收缩率估计：q ≈ (r_k2 / r_k1)^(1/(k2-k1))，几何收敛时 q ≈ |λ2/λ1|
    k1, _, r1 = history[-51]
    k2, _, r2 = history[-1]
    if r1 > 0.0 and r2 > 0.0:
        q = (r2 / r1) ** (1.0 / (k2 - k1))
        if q > 0.99:
            notes.append(
                "收敛缓慢：残差收缩率 q≈%.6f（几何收敛时 q≈|λ2/λ1|），"
                "主特征值与次特征值幅值接近" % q)
    # 符号振荡：λ 近似在正负之间来回跳，提示 λ1 ≈ -λ2
    tail = [lam for _, lam, _ in history[-40:]]
    flips = sum(1 for a, b in zip(tail, tail[1:]) if a * b < 0.0)
    if flips >= 20:
        notes.append(
            "特征值近似在正负间振荡：疑似 λ1≈-λ2（幅值相同、符号相反），"
            "幂迭代不收敛于单一特征向量，建议改用移位/块方法")
    return notes


def power_iteration(A, tol=1e-10, max_iter=10000, x0=None, seed=0,
                    verbose=False, log_every=1):
    """幂迭代主过程。

    返回 dict：
      lambda     主特征值近似（Rayleigh 商）
      vector     对应的单位特征向量
      iterations 实际迭代轮数
      status     'converged' | 'max_iter_exceeded'
      residual   最终相对残差
      history    [(k, λ_k, rel_res_k), ...]
      notes      退化情形说明（中文）
      elapsed    耗时（秒）
    """
    n = len(A) if not callable(A) else len(x0) if x0 is not None else None
    if n is None:
        raise ValueError("A 为可调用对象时必须提供 x0 以确定维度")
    rng = random.Random(seed)
    x = list(x0) if x0 is not None else [rng.uniform(-1.0, 1.0) for _ in range(n)]
    nx = norm(x)
    if nx == 0.0:
        raise ValueError("初始向量为零向量")
    x = [xi / nx for xi in x]

    history = []
    notes = []
    lam = 0.0
    rel = float("inf")
    status = "max_iter_exceeded"
    x_prev2 = None
    t0 = time.perf_counter()
    k = 0
    for k in range(1, max_iter + 1):
        Ax = matvec(A, x)
        nax = norm(Ax)
        if nax == 0.0:
            # A x = 0：零矩阵或幂零方向，主特征值为 0，任何向量都是特征向量
            lam = 0.0
            rel = 0.0
            status = "converged"
            notes.append("A·x = 0：矩阵为零矩阵或在该方向幂零，主特征值 λ=0，"
                         "特征向量不唯一")
            history.append((k, lam, rel))
            break
        lam = dot(x, Ax)                      # Rayleigh 商（x 已单位化）
        r = norm([Ax[i] - lam * x[i] for i in range(n)])
        if abs(lam) > 1e-14:
            rel = r / abs(lam)                # 相对残差
        else:
            rel = r                           # λ≈0 时相对残差无意义，退化为绝对残差
        history.append((k, lam, rel))
        if verbose and (k % log_every == 0 or rel < tol):
            print("iter %6d  lambda ≈ %+.12e  rel_residual ≈ %.3e" % (k, lam, rel))
        if rel < tol:
            status = "converged"
            break
        x_new = [ai / nax for ai in Ax]
        if x_prev2 is not None:
            # 周期-2 检测：x_{k+2}≈x_k 且残差不降 → λ1≈-λ2，迭代在
            # 两个方向间来回跳，永远不会收敛（负单主特征值时残差会趋于 0，
            # 不会被误判）
            period2 = norm([x_new[i] - x_prev2[i] for i in range(n)])
            if period2 < 1e-10 and rel > 1e-4:
                x = x_new
                status = "not_converged_oscillating"
                notes.append(
                    "迭代向量出现周期-2 振荡（x_{k+2}≈x_k）而残差维持在 %.2e："
                    "疑似 λ1≈-λ2（幅值相同、符号相反），幂迭代不收敛，"
                    "建议改用移位幂迭代或块方法" % rel)
                break
        x_prev2 = x
        x = x_new
    elapsed = time.perf_counter() - t0

    notes.extend(_analyze_history(history))
    if status == "max_iter_exceeded":
        if any("振荡" in note for note in notes):
            status = "not_converged_oscillating"
        else:
            notes.append("达到最大迭代轮数 %d 仍未收敛到 tol=%.1e"
                         % (max_iter, tol))
    return {
        "lambda": lam,
        "vector": x,
        "iterations": k,
        "status": status,
        "residual": rel,
        "history": history,
        "notes": notes,
        "elapsed": elapsed,
    }


def dominant_eigenpair(A, tol=1e-10, max_iter=10000, x0=None, seed=0,
                       verbose=False, log_every=1, verify_restart=True):
    """带退化校验的主特征对求解入口。

    收敛后用另一随机初始向量重启一次：若重启得到幅值更大的特征值，
    说明初始向量与主特征向量（近似）正交、首次收敛到了次特征对，
    自动采用重启结果并在 notes 中说明。
    """
    res = power_iteration(A, tol=tol, max_iter=max_iter, x0=x0, seed=seed,
                          verbose=verbose, log_every=log_every)
    if verify_restart and res["status"] == "converged" and res["lambda"] != 0.0:
        check = power_iteration(A, tol=tol, max_iter=max_iter, x0=None,
                                seed=seed + 1)
        if abs(check["lambda"]) > abs(res["lambda"]) * (1.0 + 1e-6):
            note = ("初始向量与主特征向量（近似）正交：首次收敛到次特征值 "
                    "%.12g，随机重启得到主特征值 %.12g，已采用重启结果"
                    % (res["lambda"], check["lambda"]))
            check["notes"] = res["notes"] + [note] + check["notes"]
            check["first_lambda"] = res["lambda"]
            return check
    return res


if __name__ == "__main__":
    # 演示：3x3 对称矩阵，逐轮输出特征值近似与相对残差
    A = to_sparse([[2.0, 1.0, 0.0],
                   [1.0, 3.0, 1.0],
                   [0.0, 1.0, 2.0]])
    print("演示：3x3 对称矩阵的幂迭代（tol=1e-12）")
    result = dominant_eigenpair(A, tol=1e-12, verbose=True)
    print("收敛：λ ≈ %.12f，迭代 %d 轮，状态 %s"
          % (result["lambda"], result["iterations"], result["status"]))
    for note in result["notes"]:
        print("说明：", note)
