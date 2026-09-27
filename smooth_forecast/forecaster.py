"""Forecaster：拟合 + 预测 + 预测区间，含边界情形降级与告警。"""
import math
from dataclasses import dataclass, field

from .fit import one_step_sse, fit_params, select_model
from .models import (Params, SEASONAL_MODELS, MODEL_SES, forecast,
                     variance_multipliers)
from .stats import mean, stdev, normal_quantile

WARN_TOO_SHORT = "序列过短：已降级为均值预测，区间基于有限样本，可信度低"
WARN_NO_SEASON = "数据不足两个季节周期：已忽略季节成分"
WARN_CONSTANT = "序列近似常数：残差方差接近 0，预测区间极窄，对突发变化无保护"
WARN_TREND_SHIFT = "近期残差持续偏置：可能存在趋势/水平突变，建议缩短历史窗口或启用阻尼趋势"
WARN_NONPOSITIVE = "序列含非正值：乘法季节模型不可用"


@dataclass
class ForecastResult:
    horizon: int
    point: list
    lower: list
    upper: list
    confidence: float
    model: str
    params: object
    sigma: float
    warnings: list = field(default_factory=list)


class Forecaster:
    """
    period:     季节周期（如日数据周报期取 7）；None 表示不考虑季节
    confidence: 预测区间置信水平，默认 0.95
    model:      "auto" 或 ses / holt / holt_damped / hw_add / hw_mul
    params:     固定 Params 时跳过自动拟合
    min_points: 少于此长度触发降级
    """

    def __init__(self, period=None, confidence=0.95, model="auto",
                 params=None, min_points=8):
        self.period = period
        self.confidence = confidence
        self.model = model
        self.fixed_params = params
        self.min_points = min_points
        self.warnings = []
        self._fallback_mean = None

    def fit(self, y, params=None, model=None):
        y = [float(v) for v in y]
        if len(y) == 0:
            raise ValueError("空序列")
        self.warnings = []
        self._n = len(y)
        fixed = params or self.fixed_params
        use_model = model or self.model

        # --- 降级 1：序列过短 ---
        if len(y) < self.min_points and fixed is None:
            self._fallback_mean = mean(y)
            self._sigma = max(stdev(y), 0.05 * abs(self._fallback_mean), 1e-6)
            self._model = "mean_fallback"
            self._params = None
            self._state = None
            self.warnings.append(WARN_TOO_SHORT)
            return self

        # --- 降级 2：常数序列 ---
        scale = max(abs(mean(y)), 1.0)
        if max(y) - min(y) <= 1e-9 * scale:
            self.warnings.append(WARN_CONSTANT)

        period = self.period
        if period and len(y) < 2 * period:
            if use_model in ("auto",) + SEASONAL_MODELS:
                self.warnings.append(WARN_NO_SEASON)
            period = None
        if use_model in SEASONAL_MODELS and period is None:
            use_model = "auto"
        if any(v <= 0 for v in y) and use_model in ("auto", "hw_mul"):
            if use_model == "hw_mul":
                self.warnings.append(WARN_NONPOSITIVE)
                use_model = "auto"

        # --- 拟合 ---
        if fixed is not None:
            self._params = fixed
            self._model = fixed.model
            sse, n, resid, state = one_step_sse(y, fixed, period or 1)
        elif use_model == "auto":
            self._model, self._params, sse, n = select_model(y, period)
            _, _, resid, state = one_step_sse(y, self._params, period or 1)
        else:
            self._model = use_model
            self._params, sse = fit_params(y, use_model, period or 1)
            _, n, resid, state = one_step_sse(y, self._params, period or 1)
        self._state = state
        self._sigma = math.sqrt(sse / max(n - 1, 1)) if n > 0 else 0.0

        # --- 告警：趋势/水平突变（近期残差偏置检验）---
        if len(resid) >= 12 and self._sigma > 0:
            w = min(2 * period if period else 12, 12, len(resid) // 3)
            if w >= 4:
                recent = resid[-w:]
                bias = mean(recent)
                if abs(bias) > 2.0 * self._sigma / math.sqrt(w):
                    self.warnings.append(WARN_TREND_SHIFT)
        return self

    def predict(self, horizon):
        if horizon < 1:
            raise ValueError("horizon 必须 >= 1")
        z = normal_quantile(0.5 + self.confidence / 2.0)
        if self._model == "mean_fallback":
            point = [self._fallback_mean] * horizon
            # 短序列区间随步长线性放宽，表达不确定性
            half = [z * self._sigma * math.sqrt(h) for h in range(1, horizon + 1)]
        else:
            point = forecast(self._state, self._params, horizon)
            mult = variance_multipliers(self._state, self._params, horizon)
            half = [z * self._sigma * math.sqrt(m) for m in mult]
        return ForecastResult(
            horizon=horizon,
            point=point,
            lower=[p - h for p, h in zip(point, half)],
            upper=[p + h for p, h in zip(point, half)],
            confidence=self.confidence,
            model=self._model,
            params=self._params,
            sigma=self._sigma,
            warnings=list(self.warnings),
        )
