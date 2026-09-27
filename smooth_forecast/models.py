"""指数平滑状态空间模型：水平（SES）/ 趋势（Holt，可阻尼）/ 季节（Holt-Winters）。

统一采用误差修正（error-correction）形式，便于做脉冲响应方差计算。
"""
import copy
from dataclasses import dataclass, field

from .stats import mean

MODEL_SES = "ses"                # 仅水平
MODEL_HOLT = "holt"              # 水平 + 线性趋势
MODEL_HOLT_DAMPED = "holt_damped"  # 水平 + 阻尼趋势
MODEL_HW_ADD = "hw_add"          # 水平 + 趋势 + 加法季节
MODEL_HW_MUL = "hw_mul"          # 水平 + 趋势 + 乘法季节

ALL_MODELS = (MODEL_SES, MODEL_HOLT, MODEL_HOLT_DAMPED, MODEL_HW_ADD, MODEL_HW_MUL)
SEASONAL_MODELS = (MODEL_HW_ADD, MODEL_HW_MUL)

_EPS = 1e-9


@dataclass
class Params:
    """平滑参数。alpha: 水平, beta: 趋势, gamma: 季节, phi: 趋势阻尼系数。"""
    model: str
    alpha: float
    beta: float = 0.0
    gamma: float = 0.0
    phi: float = 1.0

    def n_free(self):
        k = 1
        if self.model != MODEL_SES:
            k += 1
        if self.model in SEASONAL_MODELS:
            k += 1
        if self.model == MODEL_HOLT_DAMPED:
            k += 1
        return k


@dataclass
class State:
    level: float
    trend: float
    seasonal: list = field(default_factory=list)
    pos: int = 0  # 下一个观测对应的季节下标

    def copy(self):
        return State(self.level, self.trend, list(self.seasonal), self.pos)


def _trend_sum(phi, k):
    """sum_{i=1..k} phi^i"""
    if abs(phi - 1.0) < 1e-12:
        return float(k)
    return phi * (1.0 - phi ** k) / (1.0 - phi)


def init_state(y, model, period):
    """启发式初始化状态。"""
    if model == MODEL_SES:
        return State(y[0], 0.0)
    if model in (MODEL_HOLT, MODEL_HOLT_DAMPED):
        k = min(10, len(y))
        b = (y[k - 1] - y[0]) / (k - 1) if k > 1 else 0.0
        return State(y[0], b)
    m = period
    first = y[:m]
    l0 = mean(first)
    b0 = (mean(y[m:2 * m]) - l0) / m if len(y) >= 2 * m else 0.0
    if model == MODEL_HW_ADD:
        s = [v - l0 for v in first]
    else:
        s = [v / l0 if abs(l0) > _EPS else 1.0 for v in first]
    return State(l0, b0, s, 0)


def forecast_mean(state, params, k=1):
    """从当前状态出发的 k 步预测均值。"""
    if params.model == MODEL_SES:
        return state.level
    ts = _trend_sum(params.phi, k)
    base = state.level + ts * state.trend
    if params.model in (MODEL_HOLT, MODEL_HOLT_DAMPED):
        return base
    m = len(state.seasonal)
    s = state.seasonal[(state.pos + k - 1) % m]
    if params.model == MODEL_HW_ADD:
        return base + s
    return base * s


def forecast(state, params, horizon):
    return [forecast_mean(state, params, k) for k in range(1, horizon + 1)]


def update(state, params, error):
    """用观测误差 e = y - mu 更新状态（原地），并把时间推进一格。"""
    a, b, g, phi = params.alpha, params.beta, params.gamma, params.phi
    if params.model == MODEL_SES:
        state.level += a * error
        return
    if params.model in (MODEL_HOLT, MODEL_HOLT_DAMPED):
        new_level = state.level + phi * state.trend + a * error
        state.trend = phi * state.trend + a * b * error
        state.level = new_level
        return
    idx = state.pos
    s = state.seasonal[idx]
    if params.model == MODEL_HW_ADD:
        new_level = state.level + phi * state.trend + a * error
        state.trend = phi * state.trend + a * b * error
        state.level = new_level
        state.seasonal[idx] = s + g * error
    else:  # hw_mul
        denom = s if abs(s) > _EPS else _EPS
        base = state.level + phi * state.trend
        base_d = base if abs(base) > _EPS else _EPS
        new_level = base + a * error / denom
        state.trend = phi * state.trend + a * b * error / denom
        state.level = new_level
        state.seasonal[idx] = s + g * error / base_d
    state.pos = (state.pos + 1) % len(state.seasonal)


def variance_multipliers(state, params, horizon, delta=1.0):
    """h 步预测误差的方差乘子 m_h：Var(e_h) = sigma^2 * m_h。

    用脉冲响应法：在未来第 j 步注入单位误差，观察 h 步预测的变化量 w_j，
    则 m_h = 1 + sum_{j<h} w_j^2（对加法模型精确，对乘法模型为一阶近似）。
    """
    base = forecast(state, params, horizon)
    w_sq = [0.0] * (horizon + 1)
    for j in range(1, horizon):
        s = state.copy()
        for step in range(1, horizon):
            update(s, params, delta if step == j else 0.0)
            h = step + 1  # 当前状态的一步预测即原点的 h 步预测
            if h > j:
                w = (forecast_mean(s, params, 1) - base[h - 1]) / delta
                w_sq[h] += w * w
    return [1.0 + w_sq[h] for h in range(1, horizon + 1)]
