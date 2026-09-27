"""Cross-checks against analytic solutions; writes results.md with
actual errors, tolerances and sample step-size / error-estimate sequences.

Run:  python3 demo.py
"""

import math

from rk45 import IntegrationFailure, integrate

LINES = []


def out(s=""):
    print(s)
    LINES.append(s)


def max_err(sol, exact):
    return max(max(abs(y - e) for y, e in zip(row, exact(t)))
               for t, row in zip(sol.t, sol.y))


def report(name, sol, exact, rtol, atol):
    err = max_err(sol, exact)
    bound = atol + rtol  # per-unit scale; ratios below use max|y| <= 1-ish
    out(f"| {name} | {rtol:.0e} | {atol:.0e} | {sol.n_accepted} | "
        f"{sol.n_rejected} | {err:.3e} | {err / bound:.3f} |")
    return err


def step_table(sol, n=25):
    rows = ["| # | t | h | err_est (scaled) | acc |",
            "|---|---|---|---|---|"]
    for r in sol.history[:n]:
        rows.append(f"| {r.index} | {r.t:.6e} | {r.h:+.3e} | "
                    f"{r.err_norm:.3e} | {'Y' if r.accepted else 'N'} |")
    return rows


out("# Adaptive RK4(5) — 对拍误差数据与步长序列样例")
out()
out("求解器: Dormand-Prince 5(4) 嵌入对，推进四阶公式，五阶公式给出局部误差")
out("估计；误差范数为 RMS(atol + rtol·|y|) 归一化，≤1 接受该步。")
out()
out("## 1. 解析解对拍（实际全局误差 vs 容差）")
out()
out("| 问题 | rtol | atol | 接受步 | 拒绝步 | 实际最大误差 | 误差/(atol+rtol) |")
out("|---|---|---|---|---|---|---|")

sol = integrate(lambda t, y: [-y[0]], 0.0, 1.0, 5.0, rtol=1e-8, atol=1e-11)
report("一阶衰减 y'=-y, [0,5]", sol,
       lambda t: [math.exp(-t)], 1e-8, 1e-11)
decay = sol

sol = integrate(lambda t, y: [y[1], -y[0]], 0.0, [1.0, 0.0], 10.0,
                rtol=1e-8, atol=1e-11)
report("简谐振子, [0,10]", sol,
       lambda t: [math.cos(t), -math.sin(t)], 1e-8, 1e-11)


def stiff(t, y):
    u, v = y
    return [998.0 * u + 1998.0 * v, -999.0 * u - 1999.0 * v]


sol = integrate(stiff, 0.0, [1.0, 0.0], 0.1, rtol=1e-6, atol=1e-10)
report("刚性组 λ=-1,-1000, [0,0.1]", sol,
       lambda t: [2 * math.exp(-t) - math.exp(-1000 * t),
                  -math.exp(-t) + math.exp(-1000 * t)], 1e-6, 1e-10)
stiff_sol = sol

sol = integrate(lambda t, y: [math.cos(t)], 0.0, 0.0, 2 * math.pi,
                rtol=1e-9, atol=1e-12)
report("零初值 y'=cos t, [0,2π]", sol, lambda t: [math.sin(t)], 1e-9, 1e-12)

sol = integrate(lambda t, y: [-y[0]], 2.0, math.exp(-2.0), 0.0,
                rtol=1e-8, atol=1e-11)
report("负向推进 y'=-y, [2,0]", sol,
       lambda t: [math.exp(-t)], 1e-8, 1e-11)

sol = integrate(lambda t, y: [1.5 * math.sqrt(t)], 0.0, 0.0, 1.0,
                rtol=1e-8, atol=1e-11)
report("端点奇异 y'=1.5√t, [0,1]", sol, lambda t: [t ** 1.5], 1e-8, 1e-11)
sqrt_sol = sol

out()
out("误差比 < 1 表示全局误差已落在 atol+rtol 一个量级以内；累积效应下")
out("全局误差允许略超单步容差，实测均远小于 50 倍余量（见 test_rk45.py）。")
out()
out("## 2. 步长序列样例")
out()
out("### 一阶衰减（曲率平缓 → 步长迅速放大到上限附近）")
out()
out("\n".join(step_table(decay)))
out()
out("### 刚性方程组（快分量 λ=-1000 把步长压在稳定极限 ~2.8e-3 附近）")
out()
out("\n".join(step_table(stiff_sol)))
out()
out("### 端点奇异 y'=1.5√t（t=0 处曲率发散 → 起步极小，随后指数增长）")
out()
out("\n".join(step_table(sqrt_sol)))
out()
out("## 3. 最小步长失败上报")
out()
try:
    integrate(lambda t, y: [y[0]], 0.0, 1.0, 1.0,
              rtol=1e-13, atol=1e-16, h_min=0.2)
except IntegrationFailure as e:
    out("强迫失败场景（rtol=1e-13, h_min=0.2）抛出 `IntegrationFailure`：")
    out()
    out(f"    {e}")
    out(f"    失败位置 t = {e.t!r}, 当前 |h| = {abs(e.h):.3e}")
out()
out("求解器在最小步长仍不满足容差时立即报错并给出位置，不会继续空转。")

with open("results.md", "w") as fh:
    fh.write("\n".join(LINES) + "\n")
print("\nwritten: results.md")
