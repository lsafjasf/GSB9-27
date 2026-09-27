"""平滑参数自动拟合：网格搜索 + 局部细化，最小化样本内一步预测误差 SSE。"""
import math

from .models import (ALL_MODELS, SEASONAL_MODELS, MODEL_SES, MODEL_HW_MUL,
                     Params, init_state, forecast_mean, update)

_ALPHA_GRID = (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95)
_BETA_GRID = (0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5)
_GAMMA_GRID = (0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7)
_PHI_GRID = (0.80, 0.85, 0.90, 0.95, 0.98)


def one_step_sse(y, params, period, burn_in=None):
    """返回 (SSE, 有效误差数, 残差列表, 末端状态)。一步预测误差，跳过 burn-in。"""
    m = period if params.model in SEASONAL_MODELS else 0
    if burn_in is None:
        burn_in = max(m, 5)
    state = init_state(y, params.model, period)
    sse, n = 0.0, 0
    resid = []
    for i in range(max(m, 1), len(y)):
        e = y[i] - forecast_mean(state, params, 1)
        update(state, params, e)
        if i >= burn_in:
            sse += e * e
            n += 1
            resid.append(e)
    return sse, n, resid, state


def fit_params(y, model, period=1):
    """对指定模型网格搜索最优平滑参数，目标：最小化一步回测误差 SSE。"""
    def sse_of(p):
        return one_step_sse(y, p, period)[0]

    best, best_sse = None, float("inf")
    for alpha in _ALPHA_GRID:
        if model == MODEL_SES:
            cands = [Params(model, alpha)]
        elif model in SEASONAL_MODELS:
            cands = [Params(model, alpha, b, g)
                     for b in _BETA_GRID for g in _GAMMA_GRID]
        elif model == "holt_damped":
            cands = [Params(model, alpha, b, 0.0, phi)
                     for b in _BETA_GRID for phi in _PHI_GRID]
        else:
            cands = [Params(model, alpha, b) for b in _BETA_GRID]
        for p in cands:
            s = sse_of(p)
            if s < best_sse:
                best, best_sse = p, s

    # 局部细化：围绕最优点做两轮坐标细化
    for step in (0.04, 0.015):
        improved = True
        while improved:
            improved = False
            for attr in ("alpha", "beta", "gamma", "phi"):
                cur = getattr(best, attr)
                if cur == 0.0 and attr != "phi":
                    continue
                if attr == "phi" and best.model != "holt_damped":
                    continue
                lo, hi = (0.8, 0.99) if attr == "phi" else (0.01, 0.99)
                for cand in (cur - step, cur + step):
                    if not lo <= cand <= hi:
                        continue
                    p = Params(best.model, best.alpha, best.beta, best.gamma, best.phi)
                    setattr(p, attr, round(cand, 4))
                    s = sse_of(p)
                    if s < best_sse:
                        best, best_sse = p, s
                        improved = True
    return best, best_sse


def select_model(y, period=None):
    """在候选模型间用 AIC 选择（对复杂度施罚），返回 (model, params, sse, n)。"""
    candidates = [MODEL_SES, "holt", "holt_damped"]
    if period and period >= 2 and len(y) >= 2 * period:
        candidates += ["hw_add"]
        if all(v > 0 for v in y):
            candidates.append(MODEL_HW_MUL)
    best = None
    for model in candidates:
        params, sse = fit_params(y, model, period or 1)
        _, n, _, _ = one_step_sse(y, params, period or 1)
        if n < 2 or sse <= 0:
            aic = -1e18  # 完美拟合（如常数序列），直接采用
        else:
            aic = n * math.log(sse / n) + 2 * params.n_free()
        if best is None or aic < best[0]:
            best = (aic, model, params, sse, n)
    _, model, params, sse, n = best
    return model, params, sse, n
