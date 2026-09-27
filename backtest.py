"""Rolling-origin backtest: compares parameter configurations on several
synthetic scenarios and reports error metrics + interval coverage.

Run: python3 backtest.py
"""

import math
import random

from forecast import DEFAULT_GRID, FINE_GRID, rolling_backtest

HORIZON = 8
CONFIDENCE = 0.95


def make_datasets():
    ds = {}

    rng = random.Random(42)
    ds["trend+seasonal (n=120, m=12)"] = (
        [60 + 0.4 * t + 8 * math.sin(2 * math.pi * t / 12) + rng.gauss(0, 2)
         for t in range(120)], 12)

    rng = random.Random(43)
    ds["level only (n=100)"] = (
        [50 + rng.gauss(0, 2) for _ in range(100)], None)

    rng = random.Random(44)
    brk = [20 + 0.3 * t + rng.gauss(0, 1.5) for t in range(80)]
    base = 20 + 0.3 * 80
    brk += [base + 1.5 * k + rng.gauss(0, 1.5) for k in range(1, 41)]
    ds["trend break @t=80 (n=120)"] = (brk, None)

    ds["constant (n=80)"] = ([42.0] * 80, None)

    rng = random.Random(46)
    ds["short (n=10)"] = ([30 + 0.5 * t + rng.gauss(0, 1)
                           for t in range(10)], None)
    return ds


CONFIGS = [
    ("auto default grid", {}),
    ("auto fine grid", {"grid": FINE_GRID}),
    ("fixed 0.3/0.1/0.1", {"fixed_params": (0.3, 0.1, 0.1)}),
    ("no seasonal comp.", {"drop_season": True}),
]


def run():
    datasets = make_datasets()
    print(f"Rolling-origin backtest | horizon={HORIZON} "
          f"confidence={CONFIDENCE:.0%} | step=2\n")
    for name, (y, m) in datasets.items():
        print(f"### {name}")
        print(f"{'config':<22}{'origins':>8}{'MAE':>9}{'RMSE':>9}"
              f"{'MAPE%':>9}{'coverage':>10}")
        for cfg_name, opts in CONFIGS:
            season = None if opts.pop("drop_season", False) else m
            min_train = max(2 * (season or 0), 6, len(y) // 2)
            try:
                bt = rolling_backtest(
                    y, HORIZON, season_length=season, confidence=CONFIDENCE,
                    min_train=min_train, step=2, **opts)
                print(f"{cfg_name:<22}{bt.n_origins:>8}{bt.mae:>9.3f}"
                      f"{bt.rmse:>9.3f}{bt.mape:>9.2f}{bt.coverage:>10.1%}")
            except ValueError as exc:
                print(f"{cfg_name:<22}  skipped: {exc}")
        print()

    # per-horizon detail for the default config on the main scenario
    y, m = datasets["trend+seasonal (n=120, m=12)"]
    bt = rolling_backtest(y, HORIZON, season_length=m, confidence=CONFIDENCE,
                          min_train=60, step=2)
    print("### per-horizon detail: trend+seasonal, auto default grid")
    print(f"{'h':>4}{'MAE':>9}{'RMSE':>9}{'coverage':>10}")
    for row in bt.per_horizon:
        print(f"{row['h']:>4}{row['mae']:>9.3f}{row['rmse']:>9.3f}"
              f"{row['coverage']:>10.1%}")


if __name__ == "__main__":
    run()
