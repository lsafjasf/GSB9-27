"""求根库对拍自测：与解析可求根的方程对比，统计函数求值次数。

运行：python3 test_roots.py
输出：控制台结果表格 + results.csv（对拍数据与求值次数统计）
"""

import csv
import math
import sys

from roots import Status, solve

FAILURES = []


def check(name, expect_status, analytic=None, err_tol=None, expect_multi=None,
          root_must_be_none=False, **kwargs):
    r = solve(**kwargs)
    err = abs(r.root - analytic) if (r.root is not None and analytic is not None) else None

    ok = r.status is expect_status
    if ok and r.status is Status.CONVERGED and analytic is not None:
        ok = err is not None and err <= err_tol
    if ok and expect_multi is not None:
        ok = r.multiple_root_suspected == expect_multi
    if ok and root_must_be_none:
        ok = r.root is None

    if not ok:
        FAILURES.append(name)

    return {
        "case": name,
        "status": r.status.value,
        "root": r.root,
        "residual": r.residual,
        "analytic": analytic,
        "abs_error": err,
        "iterations": r.iterations,
        "f_evals": r.f_evals,
        "multi_suspected": r.multiple_root_suspected,
        "ok": "PASS" if ok else "FAIL",
        "message": r.message,
    }


def main():
    # 解析根：Cardano 公式求 x^3 - 2x + 2 = 0 的唯一实根
    disc = math.sqrt(1.0 - 8.0 / 27.0)
    cubic_root = math.cbrt(-1.0 + disc) + math.cbrt(-1.0 - disc)

    rows = [
        # --- 单根 ---
        check("单根 x^2-2 [0,2] 割线", Status.CONVERGED, analytic=math.sqrt(2),
              err_tol=1e-9, expect_multi=False,
              f=lambda x: x * x - 2.0, a=0.0, b=2.0),
        check("单根 x^2-2 [0,2] 解析导数", Status.CONVERGED, analytic=math.sqrt(2),
              err_tol=1e-9, expect_multi=False,
              f=lambda x: x * x - 2.0, df=lambda x: 2.0 * x, a=0.0, b=2.0),
        check("单根 e^x-2 仅初值 x0=0", Status.CONVERGED, analytic=math.log(2),
              err_tol=1e-9, expect_multi=False,
              f=lambda x: math.exp(x) - 2.0, x0=0.0),
        check("单根 sin(x) [3,4]", Status.CONVERGED, analytic=math.pi,
              err_tol=1e-9, expect_multi=False,
              f=math.sin, a=3.0, b=4.0),
        # --- 多根 / 平台 ---
        check("三重根 x^3 [-1,1]", Status.CONVERGED, analytic=0.0,
              err_tol=1e-9, expect_multi=True,
              f=lambda x: x ** 3, a=-1.0, b=1.0),
        check("奇重根 (x-1)^3 [0,2]", Status.CONVERGED, analytic=1.0,
              err_tol=1e-8, expect_multi=True,
              f=lambda x: (x - 1.0) ** 3, a=0.0, b=2.0),
        check("偶重根 (x-1.1)^2 [0,2] 无变号", Status.CONVERGED, analytic=1.1,
              err_tol=1e-4, expect_multi=True,
              f=lambda x: (x - 1.1) ** 2, a=0.0, b=2.0),
        # --- 无实根 / 导数近零 ---
        check("无实根 x^2+1 [-5,5]", Status.NO_ROOT_IN_INTERVAL,
              f=lambda x: x * x + 1.0, a=-5.0, b=5.0),
        check("导数近零无根 (x-1.1)^2+1e-8 [0,2]", Status.MAX_ITER_EXCEEDED,
              root_must_be_none=True,
              f=lambda x: (x - 1.1) ** 2 + 1e-8, a=0.0, b=2.0),
        # --- 极窄区间 ---
        check("极窄区间 x^2-1e-16 [0,1e-6]", Status.CONVERGED, analytic=1e-8,
              err_tol=1e-12,
              f=lambda x: x * x - 1e-16, a=0.0, b=1e-6,
              xtol=1e-18, ftol=1e-24),
        # --- 切线法失效自动回退 ---
        check("牛顿发散回退 atan(x) x0=10", Status.CONVERGED, analytic=0.0,
              err_tol=1e-9, expect_multi=False,
              f=math.atan, x0=10.0),
        check("牛顿循环回退 x^3-2x+2 x0=0", Status.CONVERGED, analytic=cubic_root,
              err_tol=1e-9, expect_multi=False,
              f=lambda x: x ** 3 - 2.0 * x + 2.0, x0=0.0),
    ]

    # 控制台表格
    hdr = ("用例", "状态", "根估计", "残差|f|", "解析根", "误差", "迭代", "f求值", "判定")
    fmt = "{:<34} {:<20} {:<22} {:<12} {:<22} {:<10} {:>4} {:>6} {:<5}"
    print(fmt.format(*hdr))
    print("-" * 140)
    for r in rows:
        print(fmt.format(
            r["case"], r["status"],
            "-" if r["root"] is None else "%.17g" % r["root"],
            "-" if r["residual"] is None else "%.3e" % r["residual"],
            "-" if r["analytic"] is None else "%.17g" % r["analytic"],
            "-" if r["abs_error"] is None else "%.2e" % r["abs_error"],
            r["iterations"], r["f_evals"], r["ok"]))
        print("    备注: multi_suspected=%s | %s" % (r["multi_suspected"], r["message"]))

    # 求值次数统计
    conv = [r["f_evals"] for r in rows if r["status"] == "converged"]
    print("\n求值次数统计（收敛用例）: 总=%d, 平均=%.1f, 最小=%d, 最大=%d"
          % (sum(conv), sum(conv) / len(conv), min(conv), max(conv)))
    print("全部用例 f 求值总次数: %d" % sum(r["f_evals"] for r in rows))

    # 对拍数据落盘
    with open("results.csv", "w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("对拍数据已写入 results.csv")

    if FAILURES:
        print("\n失败用例: %s" % ", ".join(FAILURES))
        return 1
    print("\n全部 %d 个用例通过。" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
