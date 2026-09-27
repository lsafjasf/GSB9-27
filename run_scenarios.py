"""生成场景数据并落盘对拍/残差 CSV（data/*.csv）。

场景:
  1 noise_jump  : 观测噪声方差在 k=300 从 1.0 跳到 25.0，含 5 个注入跳变。
  2 long_missing: 中段 150 步缺测。
  3 far_init    : 初值偏离真值 1000。
  4 constant_ls : q=0 常值真值，与离线最小二乘（观测均值）对拍。

每个 CSV 含: k, truth, obs(空为缺测), estimate, variance, residual,
nis, weight, missing, outlier, ref_estimate, ref_variance, ref_residual。
"""

import csv
import math
import os
import random

from kalman1d import Kalman1D
from test_kalman1d import reference_filter, make_truth

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FIELDS = ["k", "truth", "obs", "estimate", "variance", "residual", "nis",
          "weight", "missing", "outlier",
          "ref_estimate", "ref_variance", "ref_residual"]


def write_scenario(name, truth, zs, rs, x0, p0, q, r_default,
                   outlier_strategy="inflate", constant_ls=False):
    kf = Kalman1D(x0=x0, p0=p0, q=q, r=r_default,
                  outlier_strategy=outlier_strategy)
    kf_ref = Kalman1D(x0=x0, p0=p0, q=q, r=r_default, gate=float("inf"))
    kf_ref_x, kf_ref_P = [], []
    rows = []
    for k, z in enumerate(zs):
        res = kf.step(z, r=rs[k])
        kf_ref.step(z, r=rs[k])
        kf_ref_x.append(kf_ref.x)
        kf_ref_P.append(kf_ref.P)
        rows.append([k, truth[k], "" if z is None else f"{z:.6f}",
                     f"{res.estimate:.6f}", f"{res.variance:.6e}",
                     "" if res.residual is None else f"{res.residual:.6f}",
                     "" if res.nis is None else f"{res.nis:.6f}",
                     f"{res.weight:.6f}", int(res.missing), int(res.outlier)])
    ref = reference_filter(zs, x0, p0, q, rs)
    # 纯递推对拍: 关闭门限的库输出必须与手工参照逐步一致
    for k, (rx, rP, _) in enumerate(ref):
        assert abs(kf_ref_x[k] - rx) < 1e-8 * max(1.0, abs(rx)), f"k={k}"
        assert abs(kf_ref_P[k] - rP) < 1e-8 * max(1.0, abs(rP)), f"k={k}"
    for row, (rx, rP, rres) in zip(rows, ref):
        row += [f"{rx:.6f}", f"{rP:.6e}",
                "" if rres is None else f"{rres:.6f}"]

    path = os.path.join(DATA_DIR, f"{name}.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(rows)

    n = len(rows)
    n_out = sum(int(r[9]) for r in rows)
    n_miss = sum(int(r[8]) for r in rows)
    max_err = max(abs(float(r[3]) - truth[i]) for i, r in enumerate(rows))
    max_ref_diff = max(abs(kf_ref_x[i] - float(r[10]))
                       for i, r in enumerate(rows))
    print(f"[{name}] n={n} 缺测={n_miss} 离群={n_out} "
          f"末态|est-truth|={abs(float(rows[-1][3]) - truth[-1]):.4f} "
          f"全程最大|est-truth|={max_err:.4f} "
          f"库与参照最大偏差={max_ref_diff:.2e} -> {path}")
    if constant_ls:
        observed = [z for z in zs if z is not None]
        mean = sum(observed) / len(observed)
        print(f"  离线最小二乘: 均值={mean:.6f}, 滤波终值={rows[-1][3]}, "
              f"理论方差P=R/n={r_default / n:.6e}, 实际P={rows[-1][4]}")


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    rng = random.Random(101)

    # 1. 噪声突变 + 注入 5 个离群跳变
    n = 600
    truth = make_truth(n, seed=7)
    rs = [1.0 if k < 300 else 25.0 for k in range(n)]
    zs = [t + rng.gauss(0, math.sqrt(rs[k])) for k, t in enumerate(truth)]
    for j in (120, 200, 380, 460, 540):
        zs[j] += rng.choice([-1, 1]) * 40.0
    write_scenario("noise_jump", truth, zs, rs, 0.0, 10.0, 0.01, 1.0)

    # 2. 长时间缺失
    n = 400
    truth = make_truth(n, seed=3)
    zs = [None if 150 <= k < 300 else t + rng.gauss(0, 1.0)
          for k, t in enumerate(truth)]
    write_scenario("long_missing", truth, zs, [1.0] * n, 0.0, 1.0, 0.01, 1.0)

    # 3. 初值远离真值
    n = 300
    truth = make_truth(n, seed=13)
    zs = [t + rng.gauss(0, 1.0) for t in truth]
    write_scenario("far_init", truth, zs, [1.0] * n,
                   truth[0] + 1000.0, 1e6, 0.01, 1.0)

    # 4. q=0 常值真值，对拍离线最小二乘
    n = 500
    truth = [3.0] * n
    R = 4.0
    zs = [3.0 + rng.gauss(0, math.sqrt(R)) for _ in range(n)]
    write_scenario("constant_ls", truth, zs, [R] * n, 0.0, 1e9, 0.0, R,
                   constant_ls=True)

    print("CSV 列说明:", ", ".join(FIELDS))


if __name__ == "__main__":
    main()
