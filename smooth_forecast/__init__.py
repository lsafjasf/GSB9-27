"""smooth_forecast：三成分指数平滑预测库（仅标准库）。

用法：
    from smooth_forecast import Forecaster, rolling_backtest

    fc = Forecaster(period=7, confidence=0.95)
    fc.fit(history)
    res = fc.predict(horizon=7)   # res.point / res.lower / res.upper / res.warnings
"""
from .forecaster import Forecaster, ForecastResult
from .backtest import rolling_backtest, BacktestReport
from .models import Params

__all__ = ["Forecaster", "ForecastResult", "rolling_backtest",
           "BacktestReport", "Params"]
__version__ = "0.1.0"
