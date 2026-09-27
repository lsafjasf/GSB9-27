"""合成数据对拍：输出周期估计误差与重构/分量误差。"""

import math
import random

from tsdecompose import decompose


def synth(n, period, trend_fn, amp=3.0, noise=0.3, seed=42, missing=0.0):
    rng = random.Random(seed)
    out, true_trend, true_seasonal = [], [], []
    for i in range(n):
        t = trend_fn(i)
        s = amp * math.sin(2.0 * math.pi * i / period) if period else 0.0
        v = t + s + (rng.gauss(0.0, noise) if noise else 0.0)
        true_trend.append(t)
        true_seasonal.append(s)
        out.append(None if (missing and rng.random() < missing) else v)
    return out, true_trend, true_seasonal


def rmse(pairs):
    errs = [a - b for a, b in pairs if a is not None and b is not None]
    return math.sqrt(sum(e * e for e in errs) / len(errs)) if errs else float("nan")


def run(name, n, period, trend_fn, **kw):
    values, true_trend, true_seasonal = synth(n, period, trend_fn, **kw)
    dec = decompose(values)
    recon = rmse([(t + s + r, v)
                  for v, t, s, r in zip(values, dec.trend, dec.seasonal, dec.residual)
                  if v is not None])
    t_err = rmse(list(zip(dec.trend, true_trend)))
    s_err = rmse(list(zip(dec.seasonal, true_seasonal)))
    p_err = abs(dec.period - period) if (dec.period and period) else "-"
    print(f"{name:<28} {n:>5} {str(period):>6} {str(dec.period):>6} "
          f"{str(p_err):>8} {dec.mode:<18} {recon:>12.2e} {t_err:>10.4f} {s_err:>10.4f}")


LINEAR = lambda i: 0.05 * i + 1.0           # noqa: E731
JUMP = lambda i: 0.02 * i + (10.0 if i >= 250 else 0.0)  # noqa: E731
FLAT = lambda i: 5.0                        # noqa: E731

print(f"{'scenario':<28} {'n':>5} {'p_true':>6} {'p_est':>6} "
      f"{'p_err':>8} {'mode':<18} {'recon_rmse':>12} {'trend_rmse':>10} {'seas_rmse':>10}")
print("-" * 118)
run("linear+p24", 480, 24, LINEAR)
run("linear+p24+missing20%", 480, 24, LINEAR, missing=0.2)
run("linear+p50,n=997(不整除)", 997, 50, LINEAR)
run("trend_jump+p24", 500, 24, JUMP, noise=0.2)
run("noisy+p12(amp2/noise1)", 360, 12, LINEAR, amp=2.0, noise=1.0)
run("strong_noise+p12(amp1/noise1)", 360, 12, LINEAR, amp=1.0, noise=1.0)
run("short(n=30<p*2)", 30, 24, LINEAR)
run("constant", 200, None, FLAT, amp=0.0, noise=0.0)
run("pure_noise", 600, None, FLAT, amp=0.0, noise=1.0)
