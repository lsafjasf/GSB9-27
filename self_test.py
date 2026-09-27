#!/usr/bin/env python3
"""稳态分布计算自测 + 双路径对拍脚本（仅标准库）。

运行：python3 self_test.py
覆盖：单状态、遍历链、唯一吸收链、多吸收态吸收链、周期链、行和不为 1 等非法输入。
"""

import math
import sys

from steady_state import (
    InvalidChainError,
    NonConvergenceError,
    NonUniqueStationaryError,
    analyze_structure,
    power_iteration,
    stationary_linear,
    stationary_residual,
    steady_state,
)

RES_TOL = 1e-7
CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def assert_distribution(pi, P, label, tol=RES_TOL):
    n = len(P)
    assert len(pi) == n, f"{label}: 维度错误"
    assert all(math.isfinite(v) and v >= -tol for v in pi), f"{label}: 存在负/非有限分量 {pi}"
    assert abs(sum(pi) - 1.0) <= tol, f"{label}: 和为 {sum(pi)} 而非 1"
    res = stationary_residual(P, pi)
    assert res <= tol, f"{label}: 平稳方程残差 {res:.3e} 超过 {tol:.0e}"
    return res


def vec(pi, digits=6):
    return "[" + ", ".join(f"{v:.{digits}f}" for v in pi) + "]"


# ---------------------------- 1. 正常链：单状态 / 遍历 ----------------------------

@case("单状态链 [[1]]")
def t_single():
    P = [[1.0]]
    struct = analyze_structure(P)
    assert struct.irreducible and struct.unichain and struct.periods == [1]
    pw = power_iteration(P)
    lin = stationary_linear(P)
    assert pw.converged and pw.pi == [1.0]
    assert lin.pi == [1.0]
    assert_distribution(pw.pi, P, "单状态-幂迭代")
    assert_distribution(lin.pi, P, "单状态-线性")
    print(f"  幂迭代: {vec(pw.pi)}  ({pw.iterations} 步收敛, 残差 {pw.residual:.2e})")
    print(f"  线性解: {vec(lin.pi)}  (残差 {lin.residual:.2e})")


@case("遍历链双路径对拍 + 残差下降")
def t_ergodic():
    chains = {
        "2状态遍历链": [[0.9, 0.1], [0.4, 0.6]],
        "5状态遍历链": [
            [0.10, 0.50, 0.10, 0.20, 0.10],
            [0.30, 0.10, 0.40, 0.10, 0.10],
            [0.05, 0.25, 0.30, 0.30, 0.10],
            [0.20, 0.10, 0.20, 0.40, 0.10],
            [0.25, 0.25, 0.05, 0.15, 0.30],
        ],
    }
    for name, P in chains.items():
        struct = analyze_structure(P)
        assert struct.irreducible and struct.aperiodic, name
        lin = stationary_linear(P)
        pw = power_iteration(P, tol=1e-12)
        gap = max(abs(a - b) for a, b in zip(pw.pi, lin.pi))
        assert pw.converged, f"{name}: 幂迭代应收敛"
        assert gap <= 1e-8, f"{name}: 两路径不一致，最大差 {gap:.3e}"
        assert_distribution(pw.pi, P, f"{name}-幂迭代")
        assert_distribution(lin.pi, P, f"{name}-线性")

        print(f"\n  [{name}]")
        print(f"    幂迭代 pi = {vec(pw.pi)}  迭代 {pw.iterations} 步")
        print(f"    线性解 pi = {vec(lin.pi)}")
        print(f"    两路径最大分量差 = {gap:.3e}")
        print("    残差随迭代次数下降（||x_k P - x_k||_1）:")
        for k, r in pw.history:
            print(f"      k={k:>6d}   residual={r:.6e}")
        assert pw.history[-1][1] < 1e-10
        assert all(pw.history[i][1] >= pw.history[i + 1][1] - 1e-12
                   for i in range(len(pw.history) - 1)), "残差应单调不增"


# ---------------------------- 2. 吸收链 ----------------------------

@case("唯一吸收态的吸收链（可约但平稳分布唯一）")
def t_absorbing_unique():
    P = [[0.0, 1.0, 0.0],
         [0.0, 0.0, 1.0],
         [0.0, 0.5, 0.5]]
    struct = analyze_structure(P)
    assert not struct.irreducible and struct.unichain and struct.unique_stationary
    assert struct.closed_classes == [[1, 2]], struct.closed_classes
    lin = stationary_linear(P)
    pw = power_iteration(P, init=[1.0, 0.0, 0.0])
    assert pw.converged
    assert_distribution(pw.pi, P, "吸收链-幂迭代")
    assert_distribution(lin.pi, P, "吸收链-线性")
    assert max(abs(a - b) for a, b in zip(pw.pi, lin.pi)) < 1e-8
    print(f"  结构: 瞬态 [0] -> 闭类 {struct.closed_classes}（吸收态含自环）")
    print(f"  幂迭代 pi = {vec(pw.pi)}  ({pw.iterations} 步, 残差 {pw.residual:.2e})")
    print(f"  线性解 pi = {vec(lin.pi)}  (残差 {lin.residual:.2e})")
    print("  说明: 链可约但只有一个闭类，质量最终全部进入该闭类，平稳分布唯一。")


@case("多吸收态吸收链（平稳分布不唯一，极限依赖初值）")
def t_absorbing_multi():
    P = [[1.0, 0.0, 0.0, 0.0],
         [0.0, 1.0, 0.0, 0.0],
         [0.1, 0.2, 0.3, 0.4],
         [0.5, 0.5, 0.0, 0.0]]
    struct = analyze_structure(P)
    assert struct.closed_classes == [[0], [1]], struct.closed_classes
    assert not struct.unique_stationary

    lin = stationary_linear(P)
    assert not lin.unique and lin.rank < 4
    assert [c for c, _ in lin.class_distributions] == [[0], [1]]
    for comp, dist in lin.class_distributions:
        assert_distribution(dist, P, f"多吸收-闭类{comp}")
    print("  线性路径检测到系数矩阵秩亏缺，极值平稳分布:")
    for comp, dist in lin.class_distributions:
        print(f"    闭类 {comp}: pi = {vec(dist)}")
    print("    任意凸组合 a*[1,0,0,0]+(1-a)*[0,1,0,0] 都是平稳分布。")

    print("  幂迭代极限随初始分布变化:")
    for label, init in [("质量全在瞬态2", [0, 0, 1, 0]),
                        ("质量全在瞬态3", [0, 0, 0, 1]),
                        ("混合初值", [0, 0, 0.5, 0.5])]:
        pw = power_iteration(P, init=init, tol=1e-12)
        assert pw.converged, "吸收链本身非周期，数值上会收敛"
        assert_distribution(pw.pi, P, f"多吸收-{label}")
        print(f"    init={label}: pi -> {vec(pw.pi)}  ({pw.iterations} 步)")

    try:
        stationary_linear(P, require_unique=True)
        assert False, "应抛出 NonUniqueStationaryError"
    except NonUniqueStationaryError as e:
        print(f"  require_unique 报错: {e}")
    try:
        steady_state(P)
        assert False
    except NonUniqueStationaryError as e:
        print(f"  auto 路径报错: {type(e).__name__}: 链有多个闭类，拒绝给出唯一答案")


# ---------------------------- 3. 周期链 ----------------------------

@case("周期链（2周期 / 3周期）：幂迭代不收敛，线性解唯一")
def t_periodic():
    for name, P, period in [
        ("2周期", [[0.0, 1.0], [1.0, 0.0]], 2),
        ("3周期", [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]], 3),
    ]:
        struct = analyze_structure(P)
        assert struct.periods == [period], (name, struct.periods)
        assert not struct.ergodic_for_power and struct.unique_stationary
        lin = stationary_linear(P)
        n = len(P)
        assert lin.unique
        assert max(abs(v - 1.0 / n) for v in lin.pi) < 1e-10
        assert_distribution(lin.pi, P, f"{name}-线性")

        pw = power_iteration(P, init=[1.0] + [0.0] * (n - 1), max_iter=3000)
        assert not pw.converged, f"{name}: 点质量初值下幂迭代不应收敛"
        print(f"\n  [{name}] 线性解 pi = {vec(lin.pi)}（残差 {lin.residual:.1e}）")
        print(f"  普通幂迭代: {pw.caveat}")
        print("    残差随迭代次数（可见周期性平台/振荡，不趋于 0）:")
        for k, r in pw.history[:8]:
            print(f"      k={k:>4d}   residual={r:.6e}")
        print(f"      ... 停滞时残差仍为 {pw.residual:.4e}")

        avg = power_iteration(P, init=[1.0] + [0.0] * (n - 1),
                              average=True, max_iter=20000, tol=1e-9)
        assert avg.converged, f"{name}: Cesaro 均值应收敛"
        assert_distribution(avg.pi, P, f"{name}-Cesaro")
        print(f"  Cesaro 均值迭代: {avg.iterations} 步收敛, pi = {vec(avg.pi)}, 残差 {avg.residual:.2e}")

        point = [1.0] + [0.0] * (n - 1)
        try:
            steady_state(P, method="power", init=point)
            assert False
        except NonConvergenceError as e:
            print(f"  power 路径(点质量初值)报错: {type(e).__name__}: {e}")
        lucky = power_iteration(P)
        if lucky.converged:
            print("  注意: 均匀初值恰好落在平稳分布上时迭代“假收敛”，"
                  f"因此结构分析（周期={period}）才是可靠判据。")
        auto = steady_state(P)
        assert auto.method == "linear" and max(auto.pi) - min(auto.pi) < 1e-10
        print("  auto 路径自动回退到线性求解，返回唯一平稳分布。")


# ---------------------------- 4. 非法输入 ----------------------------

@case("非法转移矩阵必须报错")
def t_invalid():
    bad = [
        ("行和不为1", [[0.7, 0.2], [0.4, 0.6]]),
        ("非方阵", [[0.5, 0.5, 0.0], [0.0, 1.0, 0.0]]),
        ("负元素", [[-0.1, 1.1], [0.4, 0.6]]),
        ("含NaN", [[float("nan"), 1.0], [0.4, 0.6]]),
        ("含无穷", [[float("inf"), 0.0], [0.4, 0.6]]),
        ("非数值", [["a", "b"], [0.4, 0.6]]),
        ("空矩阵", []),
    ]
    for label, P in bad:
        for fn_name, fn in [("校验", analyze_structure),
                            ("幂迭代", power_iteration),
                            ("线性解", stationary_linear)]:
            try:
                fn(P)
                raise AssertionError(f"{label} 未被 {fn_name} 拦截")
            except InvalidChainError:
                pass
        print(f"  {label:>8}: InvalidChainError ✓")

    try:
        stationary_residual([[0.7, 0.2], [0.4, 0.6]], [0.5, 0.5])
        assert False
    except InvalidChainError as e:
        print(f"  残差接口同样拦截行和错误: {e}")

    try:
        power_iteration([[0.9, 0.1], [0.4, 0.6]], init=[0.7, 0.2])
        assert False
    except InvalidChainError:
        print("  非法初始分布（和不为1）: InvalidChainError ✓")


def main():
    print("=" * 72)
    print("稳态分布计算自测：幂迭代 vs 线性方程组（标准库实现）")
    print("=" * 72)
    passed = 0
    for name, fn in CASES:
        print(f"\n--- 用例: {name} ---")
        fn()
        passed += 1
    print("\n" + "=" * 72)
    print(f"全部 {passed} 组用例通过：非负、归一化、平稳方程残差断言均成立，双路径对拍一致。")
    print("=" * 72)


if __name__ == "__main__":
    sys.exit(main())
