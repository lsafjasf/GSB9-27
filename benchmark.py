"""基准脚本：不同阈值下的压缩率与耗时，并附带解析验证的实测最大偏差。

运行：python3 benchmark.py
"""

import math
import random
import time

from simplify import simplify
from verify import verify_analytic


def gen_noisy_circle(n, seed=42):
    rng = random.Random(seed)
    pts = []
    for i in range(n):
        ang = 2.0 * math.pi * i / n
        r = 10.0 + rng.uniform(-0.05, 0.05)
        pts.append((r * math.cos(ang), r * math.sin(ang)))
    return pts, True


def gen_random_walk(n, seed=7):
    rng = random.Random(seed)
    pts = []
    x = y = 0.0
    for _ in range(n):
        x += rng.uniform(-1.0, 1.0)
        y += rng.uniform(-1.0, 1.0)
        pts.append((x, y))
    return pts, False


def gen_sine(n, seed=None):
    return [(i * 0.02, math.sin(i * 0.02) + 0.02 * math.sin(i * 1.3))
            for i in range(n)], False


def run():
    n = 5000
    datasets = [
        ("noisy_circle(闭合)", gen_noisy_circle),
        ("random_walk(开放)", gen_random_walk),
        ("sine(开放)", gen_sine),
    ]
    epsilons = [0.001, 0.01, 0.1, 1.0]

    header = (f"{'曲线':<20}{'n':>6}{'epsilon':>9}{'保留':>7}"
              f"{'压缩率':>9}{'耗时ms':>9}{'实测最大偏差':>13}{'上界OK':>7}")
    print(header)
    print("-" * len(header.expandtabs()))
    for name, gen in datasets:
        pts, closed = gen(n)
        for eps in epsilons:
            t0 = time.perf_counter()
            r = simplify(pts, eps, closed=closed)
            dt = (time.perf_counter() - t0) * 1000.0
            max_dev, _, ok = verify_analytic(pts, r)
            ratio = len(r.kept_indices) / len(pts)
            print(f"{name:<20}{len(pts):>6}{eps:>9}{len(r.kept_indices):>7}"
                  f"{ratio:>9.2%}{dt:>9.1f}{max_dev:>13.3g}{'通过' if ok else '失败':>7}")


if __name__ == "__main__":
    run()
