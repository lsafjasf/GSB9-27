"""演示脚本：对拍、残差下降数据、退化链说明。

运行：python3 demo.py
"""

import random

from steady_state import (
    MarkovChainError,
    analyze_chain,
    assert_stationary_properties,
    power_iteration,
    stationary_linear,
)


def fmt(pi):
    return "[" + ", ".join(f"{x:.6f}" for x in pi) + "]"


def section(title):
    print()
    print("=" * 68)
    print(title)
    print("=" * 68)


# ---------------------------------------------------------------------------
section("1. 随机不可约链：幂迭代 vs 线性方程组 对拍 + 残差下降数据")
# ---------------------------------------------------------------------------
rng = random.Random(42)
n = 6
P = [[rng.random() + 0.05 for _ in range(n)] for _ in range(n)]
for row in P:
    s = sum(row)
    for j in range(n):
        row[j] /= s

r = power_iteration(P, tol=1e-14, record_every=1)
pi_lin = stationary_linear(P)
diff = max(abs(a - b) for a, b in zip(r["pi"], pi_lin))

print(f"链规模 n={n}，幂迭代收敛于第 {r['iterations']} 次迭代")
print(f"幂迭代结果 pi_pow = {fmt(r['pi'])}")
print(f"线性方程组 pi_lin = {fmt(pi_lin)}")
print(f"两法最大分量差   = {diff:.3e}  (< 1e-10 视为一致)")
assert_stationary_properties(P, r["pi"])
assert_stationary_properties(P, pi_lin)
print("固有性质断言通过：非负、和为 1、平稳方程残差 < 1e-8")
print()
print("残差 ||pi_k P - pi_k||_1 随迭代次数下降数据：")
print(f"{'迭代 k':>8} | {'残差':>14}")
print("-" * 26)
for k, res in r["history"]:
    print(f"{k:>8} | {res:>14.6e}")

# ---------------------------------------------------------------------------
section("2. 退化链一：单一状态")
# ---------------------------------------------------------------------------
P1 = [[1.0]]
r1 = power_iteration(P1)
print(f"P = [[1.0]]，幂迭代 pi = {fmt(r1['pi'])}，线性解 pi = {fmt(stationary_linear(P1))}")
assert_stationary_properties(P1, r1["pi"])
print("说明：唯一状态必为吸收态，稳态分布恒为 [1.0]。")

# ---------------------------------------------------------------------------
section("3. 退化链二：吸收链（唯一闭类，分布唯一）")
# ---------------------------------------------------------------------------
P_abs = [
    [1.0, 0.0, 0.0],
    [0.5, 0.3, 0.2],
    [0.1, 0.3, 0.6],
]
r_abs = power_iteration(P_abs, tol=1e-13)
pi_abs_lin = stationary_linear(P_abs)
print(f"幂迭代 pi = {fmt(r_abs['pi'])}（{r_abs['iterations']} 次迭代）")
print(f"线性解 pi = {fmt(pi_abs_lin)}")
assert_stationary_properties(P_abs, r_abs["pi"], tol=1e-9)
assert_stationary_properties(P_abs, pi_abs_lin)
info = analyze_chain(P_abs)
print(f"闭互通类：{info['closed_classes']}，唯一闭类 => 稳态分布唯一，全部质量被吸收态 0 捕获。")

# ---------------------------------------------------------------------------
section("4. 退化链三：周期链（幂迭代振荡不收敛）")
# ---------------------------------------------------------------------------
P_per = [[0.0, 1.0], [1.0, 0.0]]
r_per = power_iteration(P_per, max_iter=200, pi0=[0.9, 0.1])
print(f"周期 d=2 链，非均匀起点 [0.9, 0.1]：")
print(f"  幂迭代 converged = {r_per['converged']}")
print(f"  末几次残差 = {[f'{res:.3f}' for _, res in r_per['history'][-4:]]}（在 1.6 附近振荡，不下降）")
print(f"  判定与说明：{r_per['diagnosis']}")
r_ces = power_iteration(P_per, cesaro=True, tol=1e-10, pi0=[0.9, 0.1])
print(f"  Cesàro 平均：converged = {r_ces['converged']}，pi = {fmt(r_ces['pi'])}")
pi_per_lin = stationary_linear(P_per)
print(f"  线性方程组：pi = {fmt(pi_per_lin)}（平稳方程不依赖周期性，可直接求解）")
assert_stationary_properties(P_per, r_ces["pi"])
assert_stationary_properties(P_per, pi_per_lin)

# ---------------------------------------------------------------------------
section("5. 退化链四：可约链（多个闭类，稳态分布不唯一）")
# ---------------------------------------------------------------------------
P_red = [
    [1.0, 0.0, 0.0, 0.0],
    [0.5, 0.0, 0.5, 0.0],
    [0.0, 0.5, 0.0, 0.5],
    [0.0, 0.0, 0.0, 1.0],
]
info = analyze_chain(P_red)
print(f"赌徒破产式链，闭互通类 = {info['closed_classes']}（两个吸收态 0 和 3）")
r_red = power_iteration(P_red, tol=1e-13)
print(f"  幂迭代（均匀起点）converged = {r_red['converged']}，pi = {fmt(r_red['pi'])}")
print("  注意：幂迭代'收敛'但结果依赖初始分布 —— 从状态 1 出发与从状态 2 出发极限不同，")
print("        这是可约链'收敛到错的分布'的典型陷阱。")
r_red2 = power_iteration(P_red, tol=1e-13, pi0=[0.0, 0.0, 1.0, 0.0])
print(f"  换起点 [0,0,1,0] 后 pi = {fmt(r_red2['pi'])}（与均匀起点结果不同，证实不唯一）")
try:
    stationary_linear(P_red)
except MarkovChainError as e:
    print(f"  线性方程组正确报错：{e}")

# ---------------------------------------------------------------------------
section("6. 非法输入：概率矩阵行和不为 1（必须报错）")
# ---------------------------------------------------------------------------
P_bad = [[0.7, 0.2], [0.4, 0.6]]
for name, fn in [("power_iteration", lambda: power_iteration(P_bad)),
                 ("stationary_linear", lambda: stationary_linear(P_bad))]:
    try:
        fn()
        print(f"  {name}: 未报错 —— BUG!")
    except MarkovChainError as e:
        print(f"  {name} 正确报错：{e}")

print()
print("全部演示完成。")
