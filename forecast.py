"""Smoothing forecast library (Python 3 standard library only).

Supports three smoothing components:
  - level    : Simple Exponential Smoothing (SES)
  - trend    : Holt's linear trend
  - seasonal : Holt-Winters, additive or multiplicative

Smoothing parameters (alpha/beta/gamma) are fitted automatically by grid
search, minimising the one-step-ahead in-sample SSE (i.e. backtest error on
the training series). Prediction intervals use a per-horizon residual sigma
estimated from an internal rolling-origin backtest when the series is long
enough, otherwise the one-step sigma scaled by sqrt(h).

Degradation rules (always surfaced via Forecast.warnings):
  - n == 1 or constant series -> flat forecast, zero-width interval
  - n < 4                     -> mean forecast, sqrt(h) interval
  - n < 2 * season_length     -> seasonal component dropped
  - recent one-step errors >> historical -> structural-change warning
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist, mean, pstdev

__all__ = [
    "Forecast",
    "BacktestResult",
    "forecast",
    "rolling_backtest",
    "DEFAULT_GRID",
    "FINE_GRID",
]

# Default grid: 8 values per parameter -> up to 8^3 = 512 candidates.
DEFAULT_GRID = (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 0.9)
# Fine grid: 13 values per parameter -> up to 13^3 = 2197 candidates.
FINE_GRID = (0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.98)

MIN_FOR_MEAN = 4   # below this: fall back to the sample mean
MIN_FOR_TREND = 6  # below this: SES only, no trend component


@dataclass
class Forecast:
    values: list          # point forecasts, length == horizon
    lower: list           # lower bound of the prediction interval
    upper: list           # upper bound of the prediction interval
    model: str            # "ses" | "holt" | "hw-add" | "hw-mul" | "mean" | "constant"
    params: dict          # fitted alpha/beta/gamma (+ season_length)
    confidence: float     # nominal confidence level of the interval
    sigma: list           # per-horizon std dev used to build the interval
    warnings: list = field(default_factory=list)


@dataclass
class BacktestResult:
    horizon: int
    n_origins: int
    mae: float
    rmse: float
    mape: float           # percent; nan if every actual is zero
    coverage: float       # fraction of actuals inside the interval
    confidence: float
    per_horizon: list     # [{"h", "mae", "rmse", "coverage"}, ...]


# ---------------------------------------------------------------------------
# model fitting (each returns (sse, level, trend, seasonal, one_step_residuals))
# ---------------------------------------------------------------------------

def _fit_ses(y, alpha):
    level = y[0]
    sse = 0.0
    residuals = []
    for t in range(1, len(y)):
        err = y[t] - level
        residuals.append(err)
        sse += err * err
        level = alpha * y[t] + (1.0 - alpha) * level
    return sse, level, 0.0, None, residuals


def _fit_holt(y, alpha, beta):
    level = y[0]
    trend = y[1] - y[0]
    sse = 0.0
    residuals = []
    for t in range(1, len(y)):
        err = y[t] - (level + trend)
        residuals.append(err)
        sse += err * err
        prev = level
        level = alpha * y[t] + (1.0 - alpha) * (level + trend)
        trend = beta * (level - prev) + (1.0 - beta) * trend
    return sse, level, trend, None, residuals


def _fit_hw(y, m, alpha, beta, gamma, kind):
    """Holt-Winters. kind is "add" or "mul". Returns None if infeasible."""
    n = len(y)
    level = mean(y[:m])
    trend = (mean(y[m:2 * m]) - level) / m
    if kind == "add":
        seas = [y[i] - level for i in range(m)]
    else:
        if level == 0 or any(v <= 0 for v in y[: 2 * m]):
            return None
        seas = [y[i] / level for i in range(m)]
    sse = 0.0
    residuals = []
    for t in range(m, n):
        if kind == "add":
            f = level + trend + seas[t - m]
        else:
            f = (level + trend) * seas[t - m]
        err = y[t] - f
        residuals.append(err)
        sse += err * err
        prev = level
        if kind == "add":
            level = alpha * (y[t] - seas[t - m]) + (1.0 - alpha) * (level + trend)
            seas.append(gamma * (y[t] - level) + (1.0 - gamma) * seas[t - m])
        else:
            if seas[t - m] == 0:
                return None
            level = alpha * (y[t] / seas[t - m]) + (1.0 - alpha) * (level + trend)
            seas.append(gamma * (y[t] / level) + (1.0 - gamma) * seas[t - m])
        trend = beta * (level - prev) + (1.0 - beta) * trend
    return sse, level, trend, seas, residuals


def _fit_model(y, model, alpha, beta, gamma, season_length):
    if model == "ses":
        return _fit_ses(y, alpha)
    if model == "holt":
        return _fit_holt(y, alpha, beta)
    return _fit_hw(y, season_length, alpha, beta, gamma, model.split("-")[1])


def _select(y, season_length, seasonal, grid):
    """Grid search minimising one-step-ahead SSE. Returns (model, a, b, g)."""
    n = len(y)
    best = None

    def consider(fit, model, a, b, g):
        nonlocal best
        if fit is None:
            return
        if best is None or fit[0] < best[0]:
            best = (fit[0], model, a, b, g)

    for a in grid:
        consider(_fit_ses(y, a), "ses", a, 0.0, 0.0)
    if n >= MIN_FOR_TREND:
        for a in grid:
            for b in grid:
                consider(_fit_holt(y, a, b), "holt", a, b, 0.0)
    if season_length and n >= 2 * season_length:
        kinds = ("add", "mul") if seasonal == "auto" else (seasonal,)
        for kind in kinds:
            for a in grid:
                for b in grid:
                    for g in grid:
                        consider(
                            _fit_hw(y, season_length, a, b, g, kind),
                            "hw-" + kind, a, b, g,
                        )
    _, model, a, b, g = best
    return model, a, b, g


def _point_forecast(model, level, trend, seas, season_length, horizon):
    out = []
    for h in range(1, horizon + 1):
        if model == "ses":
            out.append(level)
        elif model == "holt":
            out.append(level + h * trend)
        else:
            idx = len(seas) - season_length + (h - 1) % season_length
            if model == "hw-add":
                out.append(level + h * trend + seas[idx])
            else:
                out.append((level + h * trend) * seas[idx])
    return out


def _rolling_sigma(y, model, params, season_length, horizon, sigma1):
    """Per-horizon error std from an internal rolling-origin backtest with the
    fitted parameters held fixed. Falls back to sigma1 * sqrt(h)."""
    n = len(y)
    alpha, beta, gamma = params
    if model.startswith("hw"):
        min_train = 2 * season_length + 1
    elif model == "holt":
        min_train = MIN_FOR_TREND + 1
    else:
        min_train = 3
    start = max(min_train, n - 4 * horizon)
    errors = [[] for _ in range(horizon)]
    for t in range(start, n):
        h_max = min(horizon, n - t)
        if h_max < 1:
            continue
        fit = _fit_model(y[:t], model, alpha, beta, gamma, season_length)
        if fit is None:
            continue
        _, level, trend, seas, _ = fit
        pts = _point_forecast(model, level, trend, seas, season_length, h_max)
        for h in range(h_max):
            errors[h].append(y[t + h] - pts[h])
    sigmas = []
    for h in range(horizon):
        if len(errors[h]) >= 5:
            sd = pstdev(errors[h])
            sigmas.append(sd if sd > 0 else sigma1 * math.sqrt(h + 1))
        else:
            sigmas.append(sigma1 * math.sqrt(h + 1))
    return sigmas


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def forecast(series, horizon, season_length=None, confidence=0.95,
             seasonal="auto", grid=DEFAULT_GRID, fixed_params=None):
    """Fit a smoothing model and forecast `horizon` steps ahead.

    series        : sequence of numbers, ordered oldest -> newest
    horizon       : number of future points to forecast
    season_length : seasonal period (e.g. 12 for monthly-in-year); None = no
                    seasonal component
    confidence    : nominal prediction-interval level, e.g. 0.95
    seasonal      : "auto" | "add" | "mul" (only used when season_length set)
    grid          : candidate values for alpha/beta/gamma
    fixed_params  : optional (alpha, beta, gamma) to skip the grid search
    """
    y = [float(v) for v in series]
    n = len(y)
    if n == 0:
        raise ValueError("empty series")
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be in (0.5, 1)")
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    warnings = []

    # --- degenerate cases -------------------------------------------------
    if n == 1 or max(y) == min(y):
        v = y[-1]
        warnings.append(
            "constant series: flat forecast with zero-width interval"
            if n > 1 else
            "single observation: flat forecast with zero-width interval"
        )
        return Forecast([v] * horizon, [v] * horizon, [v] * horizon,
                        "constant", {}, confidence, [0.0] * horizon, warnings)

    if n < MIN_FOR_MEAN:
        v = mean(y)
        sd = pstdev(y)
        warnings.append(
            f"series too short (n={n} < {MIN_FOR_MEAN}): "
            "falling back to mean forecast"
        )
        sigma = [sd * math.sqrt(h) for h in range(1, horizon + 1)]
        return Forecast([v] * horizon,
                        [v - z * s for s in sigma],
                        [v + z * s for s in sigma],
                        "mean", {}, confidence, sigma, warnings)

    if season_length and n < 2 * season_length:
        warnings.append(
            f"insufficient data for season_length={season_length} "
            f"(n={n} < {2 * season_length}): seasonal component dropped"
        )
        season_length = None

    # --- model selection / fitting ---------------------------------------
    if fixed_params is not None:
        a, b, g = (float(p) for p in fixed_params)
        if season_length:
            model = "hw-add"
        elif n >= MIN_FOR_TREND:
            model = "holt"
        else:
            model = "ses"
    else:
        model, a, b, g = _select(y, season_length, seasonal, grid)

    fit = _fit_model(y, model, a, b, g, season_length)
    if fit is None:  # multiplicative infeasible on this data
        model = "hw-add"
        fit = _fit_model(y, model, a, b, g, season_length)
        warnings.append("multiplicative seasonality infeasible (non-positive "
                        "values): fell back to additive")
    _, level, trend, seas, residuals = fit

    values = _point_forecast(model, level, trend, seas, season_length, horizon)
    if len(residuals) >= 2:
        sigma1 = pstdev(residuals)
    else:
        sigma1 = abs(residuals[0]) if residuals else 0.0
    sigma = _rolling_sigma(y, model, (a, b, g), season_length, horizon, sigma1)
    lower = [v - z * s for v, s in zip(values, sigma)]
    upper = [v + z * s for v, s in zip(values, sigma)]

    # --- structural-change (trend break) warning --------------------------
    k = max(3, n // 4)
    if len(residuals) > 2 * k:
        base = mean(abs(e) for e in residuals[:-k])
        recent = mean(abs(e) for e in residuals[-k:])
        if base > 0 and recent > 2.5 * base:
            warnings.append(
                "recent one-step errors are much larger than historical: "
                "possible level/trend structural change; treat the forecast "
                "and intervals with caution"
            )

    params = {"alpha": a, "beta": b, "gamma": g}
    if season_length:
        params["season_length"] = season_length
    return Forecast(values, lower, upper, model, params, confidence,
                    sigma, warnings)


def rolling_backtest(series, horizon, season_length=None, confidence=0.95,
                     seasonal="auto", grid=DEFAULT_GRID, fixed_params=None,
                     min_train=None, step=1):
    """Rolling-origin evaluation: at each origin t, fit on series[:t] and
    forecast up to `horizon` steps, then score against the actuals."""
    y = [float(v) for v in series]
    n = len(y)
    if min_train is None:
        min_train = max(2 * (season_length or 0), MIN_FOR_TREND, n // 2)
    errors = [[] for _ in range(horizon)]
    hits = [[] for _ in range(horizon)]
    origins = 0
    for t in range(min_train, n, step):
        h_max = min(horizon, n - t)
        if h_max < 1:
            continue
        fc = forecast(y[:t], h_max, season_length=season_length,
                      confidence=confidence, seasonal=seasonal, grid=grid,
                      fixed_params=fixed_params)
        for h in range(h_max):
            actual = y[t + h]
            errors[h].append(actual - fc.values[h])
            hits[h].append(1.0 if fc.lower[h] <= actual <= fc.upper[h] else 0.0)
        origins += 1

    flat = [e for errs in errors for e in errs]
    if not flat:
        raise ValueError("no backtest origins (series too short?)")
    mae = mean(abs(e) for e in flat)
    rmse = math.sqrt(mean(e * e for e in flat))
    ape = []
    col = [0] * horizon
    for t in range(min_train, n, step):
        h_max = min(horizon, n - t)
        for h in range(h_max):
            actual = y[t + h]
            err = errors[h][col[h]]
            col[h] += 1
            if actual != 0:
                ape.append(abs(err) / abs(actual))
    mape = 100.0 * mean(ape) if ape else float("nan")

    all_hits = [x for hs in hits for x in hs]
    coverage = mean(all_hits)
    per_horizon = []
    for h in range(horizon):
        if not errors[h]:
            continue
        per_horizon.append({
            "h": h + 1,
            "mae": mean(abs(e) for e in errors[h]),
            "rmse": math.sqrt(mean(e * e for e in errors[h])),
            "coverage": mean(hits[h]),
        })
    return BacktestResult(horizon, origins, mae, rmse, mape, coverage,
                          confidence, per_horizon)
