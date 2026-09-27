"""test_rootfind — 与解析可求根方程对拍的自测程序。

运行：python3 test_rootfind.py
覆盖：单根、多根（奇/偶重）、无实根、极窄区间、极点、导数为零、迭代上限。
所有断言通过时退出码为 0，并把对拍数据与求值次数统计写入 benchmark.md。
"""

import math
import sys

from rootfind import Status, find_root

ROWS = []   # 对拍数据行
FAIL = []   # 失败用例


def check(name, expect_status, res, true_root=None, root_tol=None, note=""):
    ok = res.status is expect_status
    root_err = ""
    if res.converged:
        # 收敛时残差必须足够小，杜绝“拿残差很大的点冒充根”
        if not (res.residual is not None and res.residual <= 1e-9):
            ok = False
        if true_root is not None:
            err = abs(res.root - true_root)
            root_err = f"{err:.3e}"
            if root_tol is not None and err > root_tol:
                ok = False
    elif res.status is Status.NO_ROOT_IN_INTERVAL:
        ok = ok and res.root is None
    ROWS.append((name, res, true_root, root_err, note))
    if not ok:
        FAIL.append(name)
    return ok


def fmt(x, spec="{:.10g}"):
    return spec.format(x) if x is not None else "-"


def main():
    # ---------- 1. 单根：x^2 - 2 = 0，根 sqrt(2)，提供解析导数 ----------
    r = find_root(lambda x: x * x - 2.0, df=lambda x: 2.0 * x, bracket=(0.0, 2.0))
    check("sqrt(2) 单根(解析导数)", Status.CONVERGED, r, math.sqrt(2), 1e-10)

    # ---------- 2. 单根：x^3 - x - 2 = 0，数值导数 ----------
    r = find_root(lambda x: x**3 - x - 2.0, bracket=(1.0, 2.0))
    check("x^3-x-2 单根(数值导数)", Status.CONVERGED, r, 1.5213797068045676, 1e-10)

    # ---------- 3. 单根：e^x - 2 = 0，根 ln 2 ----------
    r = find_root(lambda x: math.exp(x) - 2.0, bracket=(0.0, 2.0))
    check("e^x-2 单根 ln2", Status.CONVERGED, r, math.log(2.0), 1e-10)

    # ---------- 4. 三重根：(x-1)^3 = 0（奇重根，端点异号） ----------
    r = find_root(lambda x: (x - 1.0) ** 3, bracket=(0.0, 1.9))
    check("(x-1)^3 三重根", Status.CONVERGED, r, 1.0, 1e-6,
          note=f"估计重数 m={r.multiplicity}")
    if r.multiplicity != 3:
        FAIL.append("(x-1)^3 重数估计")

    # ---------- 5. 二重根：(x-1.5)^2 = 0（偶重根，端点同号，切触探测） ----------
    r = find_root(lambda x: (x - 1.5) ** 2, bracket=(0.0, 2.0))
    check("(x-1.5)^2 二重根(同号区间)", Status.CONVERGED, r, 1.5, 1e-5,
          note="端点同号，切触根探测")

    # ---------- 6. 无实根：x^2 + 1 = 0 ----------
    r = find_root(lambda x: x * x + 1.0, bracket=(-2.0, 3.0))
    check("x^2+1 无实根", Status.NO_ROOT_IN_INTERVAL, r)

    # ---------- 7. 极窄区间：sin x 在 pi 附近，区间宽 2e-9 ----------
    pi = math.pi
    r = find_root(math.sin, bracket=(pi - 1e-9, pi + 1e-9))
    check("sin x 极窄区间(宽2e-9)", Status.CONVERGED, r, pi, 1e-9)

    # ---------- 8. 无区间、仅初值：e^x - 3 = 0，根 ln 3 ----------
    r = find_root(lambda x: math.exp(x) - 3.0, x0=1.0)
    check("e^x-3 纯切线法 ln3", Status.CONVERGED, r, math.log(3.0), 1e-10)

    # ---------- 9. 导数为零且无区间：x^2 + 1，x0 = 0 ----------
    r = find_root(lambda x: x * x + 1.0, x0=0.0)
    check("x^2+1 导数为零停滞", Status.STALLED_ZERO_DERIVATIVE, r)

    # ---------- 10. 迭代上限：正常方程但 max_iter=2 ----------
    r = find_root(lambda x: x**3 - x - 2.0, bracket=(1.0, 2.0), max_iter=2)
    check("max_iter=2 迭代上限", Status.MAX_ITER_EXCEEDED, r)

    # ---------- 11. 极点：1/x 在 [-1, 2]（跨奇点，无根） ----------
    r = find_root(lambda x: 1.0 / x, bracket=(-1.0, 2.0))
    check("1/x 跨极点", Status.POLE_SUSPECTED, r)

    # ---------- 输出对拍数据与求值次数统计 ----------
    header = ("| 用例 | 状态 | 根估计 | 真值 | 根误差 | 残差 |f| | 迭代 | "
              "f 求值 | df 求值 | 备注 |")
    sep = "|" + "---|" * 10
    lines = [header, sep]
    for name, res, true_root, root_err, note in ROWS:
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                name, res.status.value, fmt(res.root), fmt(true_root),
                root_err or "-", fmt(res.residual, "{:.3e}"),
                res.iterations, res.f_evals, res.df_evals, note))
    table = "\n".join(lines)
    print(table)

    with open("benchmark.md", "w", encoding="utf-8") as fh:
        fh.write("# 对拍数据与求值次数统计\n\n")
        fh.write("容差：xtol = ftol = 1e-12，max_iter = 100（除标注外）。\n\n")
        fh.write(table + "\n")

    total_f = sum(res.f_evals for _, res, *_ in ROWS)
    total_df = sum(res.df_evals for _, res, *_ in ROWS)
    print(f"\nf 总求值次数: {total_f}, df 总求值次数: {total_df}")

    if FAIL:
        print("\nFAILED: " + ", ".join(FAIL))
        return 1
    print(f"\nALL {len(ROWS)} CASES PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
