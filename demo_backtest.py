"""滚动回测演示：多组参数对比 + 区间覆盖率检验 + 边界情形。

运行：python3 demo_backtest.py
结果同时写入 backtest_results.txt
"""
import math
import random
import sys

from smooth_forecast import Forecaster, rolling_backtest, Params

HORIZON = 3
CONFIDENCE = 0.95


def make_capacity_series(n=400, seed=42):
    """模拟容量指标：水平 100 + 日趋势 0.05 + 周季节(幅度8) + 噪声2，
    第 320 天起趋势由 0.05 变为 0.25（趋势突变）。"""
    rng = random.Random(seed)
    y = []
    for t in range(n):
        trend = 0.05 * t if t < 320 else 0.05 * 320 + 0.25 * (t - 320)
        y.append(100 + trend + 8 * math.sin(2 * math.pi * t / 7)
                 + rng.gauss(0, 2))
    return y


def make_stable_series(n=400, seed=7):
    """无突变的平稳趋势+季节序列，用于覆盖率检验。"""
    rng = random.Random(seed)
    return [100 + 0.05 * t + 8 * math.sin(2 * math.pi * t / 7)
            + rng.gauss(0, 2) for t in range(n)]


def line(out, s=""):
    print(s)
    out.append(s)


def main():
    out = []
    line(out, "=" * 88)
    line(out, f"滚动回测：horizon={HORIZON}, 置信水平={CONFIDENCE:.0%}, "
              f"min_train=60, step=1, 参数每 20 个起点重拟合")
    line(out, "=" * 88)

    # ---------- 1. 多组参数对比（平稳序列） ----------
    y = make_stable_series()
    configs = {
        "auto(自动选模型+拟合)": {},
        "ses(仅水平)": {"model": "ses"},
        "holt(趋势)": {"model": "holt"},
        "holt_damped(阻尼趋势)": {"model": "holt_damped"},
        "hw_add(加法季节)": {"model": "hw_add"},
        "hw_mul(乘法季节)": {"model": "hw_mul"},
        "固定参数a0.3b0.1g0.1": {"params": Params("hw_add", 0.3, 0.1, 0.1)},
    }
    line(out, "\n[1] 平稳趋势+周季节序列（n=400）：多组参数滚动回测对比")
    reports = rolling_backtest(y, HORIZON, configs=configs, period=7,
                               confidence=CONFIDENCE, min_train=60,
                               refit_every=20)
    for r in reports:
        line(out, "  " + r.summary())
    line(out, f"  名义置信水平 {CONFIDENCE:.0%}；覆盖率与之接近说明区间校准良好")

    best = min(reports, key=lambda r: r.mae)
    line(out, f"  -> MAE 最优：{best.name}")

    # ---------- 2. 分步长覆盖率 ----------
    line(out, "\n[2] auto 配置的分步长覆盖率与 MAE")
    r0 = reports[0]
    for h in range(HORIZON):
        line(out, f"  h={h+1}: 覆盖率={r0.per_horizon_coverage[h]:.2%} "
                  f"MAE={r0.per_horizon_mae[h]:.3f}")

    # ---------- 3. 趋势突变序列 ----------
    line(out, "\n[3] 趋势突变序列（第 320 天趋势 0.05->0.25）：阻尼 vs 非阻尼")
    y2 = make_capacity_series()
    reports2 = rolling_backtest(
        y2, HORIZON, period=7, confidence=CONFIDENCE, min_train=60,
        refit_every=20,
        configs={
            "auto": {},
            "holt(非阻尼)": {"model": "holt"},
            "holt_damped(阻尼)": {"model": "holt_damped"},
        })
    for r in reports2:
        line(out, "  " + r.summary())
        for w in r.warnings:
            line(out, f"    告警: {w}")

    # ---------- 4. 边界情形 ----------
    line(out, "\n[4] 边界情形")
    fc = Forecaster().fit([5.0, 5.5, 4.8])
    res = fc.predict(3)
    line(out, f"  过短序列(n=3): model={res.model} point={[round(p,2) for p in res.point]}")
    for w in res.warnings:
        line(out, f"    告警: {w}")

    fc = Forecaster().fit([42.0] * 50)
    res = fc.predict(3)
    line(out, f"  常数序列(n=50): point={[round(p,2) for p in res.point]} "
              f"宽度={res.upper[0]-res.lower[0]:.2e}")
    for w in res.warnings:
        line(out, f"    告警: {w}")

    y3 = make_stable_series(n=150)
    y3[140:] = [v + 25 for v in y3[140:]]
    fc = Forecaster(period=7).fit(y3)
    res = fc.predict(3)
    line(out, f"  水平突变(末端+25): model={res.model}")
    for w in res.warnings:
        line(out, f"    告警: {w}")

    text = "\n".join(out) + "\n"
    with open("backtest_results.txt", "w") as f:
        f.write(text)


if __name__ == "__main__":
    main()
