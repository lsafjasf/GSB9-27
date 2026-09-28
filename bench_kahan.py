"""误差对比（朴素 vs Kahan vs 精确值）与吞吐基准。仅标准库。"""
import random
import time
from fractions import Fraction

from kahan import KahanSum


def naive_sum(xs):
    s = 0.0
    for x in xs:
        s += x
    return s


def rel_err(got, exact):
    exact = float(exact)
    if exact == 0.0:
        return abs(got)
    return abs(got - exact) / abs(exact)


def gen(n, gap, seed=0):
    """大量级 1.0 与 小量级 10^-gap 混合（小量占 90%），精确值用 Fraction 计算。"""
    rng = random.Random(seed)
    small = 10.0 ** (-gap)
    return [small if rng.random() < 0.9 else 1.0 for _ in range(n)]


print("=== 相对误差：随序列长度变化（量级差异 gap=8）===")
print(f"{'n':>10} {'naive':>12} {'kahan':>12}")
for n in (1_000, 10_000, 100_000, 1_000_000):
    xs = gen(n, gap=8, seed=n)
    exact = sum((Fraction(x) for x in xs), Fraction(0))
    e_n = rel_err(naive_sum(xs), exact)
    e_k = rel_err(KahanSum(xs).value, exact)
    print(f"{n:>10,} {e_n:>12.3e} {e_k:>12.3e}")

print()
print("=== 相对误差：随量级差异变化（n=100,000）===")
print(f"{'gap':>6} {'naive':>12} {'kahan':>12}")
for gap in (4, 8, 12, 16):
    xs = gen(100_000, gap=gap, seed=gap)
    exact = sum((Fraction(x) for x in xs), Fraction(0))
    e_n = rel_err(naive_sum(xs), exact)
    e_k = rel_err(KahanSum(xs).value, exact)
    print(f"1e-{gap:<3} {e_n:>12.3e} {e_k:>12.3e}")

print()
print("=== 吞吐（n=1,000,000，随机均匀分布）===")
rng = random.Random(1)
xs = [rng.uniform(-1e6, 1e6) for _ in range(1_000_000)]

t0 = time.perf_counter()
naive_sum(xs)
t1 = time.perf_counter()
k = KahanSum()
k.add_many(xs)
t2 = time.perf_counter()
k2 = KahanSum()
for x in xs:
    k2.add(x)
t3 = time.perf_counter()

d_naive, d_many, d_add = t1 - t0, t2 - t1, t3 - t2
print(f"naive 循环     : {d_naive:.3f}s  {len(xs)/d_naive/1e6:8.2f} M adds/s")
print(f"kahan add_many : {d_many:.3f}s  {len(xs)/d_many/1e6:8.2f} M adds/s  (开销 {d_many/d_naive:.1f}x)")
print(f"kahan 逐次 add : {d_add:.3f}s  {len(xs)/d_add/1e6:8.2f} M adds/s  (开销 {d_add/d_naive:.1f}x)")
