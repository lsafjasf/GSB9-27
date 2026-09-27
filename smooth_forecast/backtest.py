"""滚动起点（rolling-origin）回测：误差指标 + 区间覆盖率检验。"""
import math
from dataclasses import dataclass, field

from .forecaster import Forecaster


@dataclass
class BacktestReport:
    name: str
    horizon: int
    n_origins: int
    mae: float
    rmse: float
    mape: float            # 忽略 |actual| < 1e-9 的点
    coverage: float        # 实际值落在预测区间内的比例（全部步长合并）
    mean_width: float      # 平均区间宽度
    per_horizon_coverage: list
    per_horizon_mae: list
    warnings: list = field(default_factory=list)

    def summary(self):
        return (f"{self.name:<28} MAE={self.mae:8.3f} RMSE={self.rmse:8.3f} "
                f"MAPE={self.mape:6.2%} 覆盖率={self.coverage:6.2%} "
                f"均宽={self.mean_width:8.2f} 起点数={self.n_origins}")


def rolling_backtest(y, horizon, configs=None, period=None, confidence=0.95,
                     min_train=None, step=1, refit_every=20):
    """
    对每个配置做滚动起点回测：
    起点 t 从 min_train 起，每次用 y[:t] 训练、预测 y[t:t+horizon]。
    参数每 refit_every 个起点重新网格拟合一次，其余起点仅更新状态（贴近
    生产上"周期性重拟合 + 逐点滚动"的用法，也控制计算量）。

    configs: {名称: Forecaster 关键字参数}，默认 {"auto": {}}。
    """
    y = [float(v) for v in y]
    n = len(y)
    if min_train is None:
        min_train = max(2 * (period or 1) + 10, 30)
    min_train = min(min_train, n - horizon)
    if min_train < 4:
        raise ValueError("数据太少，无法回测")
    if configs is None:
        configs = {"auto": {}}

    reports = []
    for name, kw in configs.items():
        fc = Forecaster(period=period, confidence=confidence, **kw)
        cached_params = cached_model = None
        last_fit = -10 ** 9
        abs_err = [0.0] * horizon
        sq_err = [0.0] * horizon
        ape = [0.0] * horizon
        ape_cnt = [0] * horizon
        hits = [0] * horizon
        counts = [0] * horizon
        width_sum = 0.0
        warn_set = []
        origins = 0
        for t in range(min_train, n - horizon + 1, step):
            train = y[:t]
            if cached_params is None or (t - last_fit) >= refit_every:
                fc.fit(train)
                cached_params, cached_model = fc._params, fc._model
                last_fit = t
            else:
                fc.fit(train, params=cached_params, model=cached_model)
            for w in fc.warnings:
                if w not in warn_set:
                    warn_set.append(w)
            res = fc.predict(horizon)
            for h in range(horizon):
                actual = y[t + h]
                err = actual - res.point[h]
                abs_err[h] += abs(err)
                sq_err[h] += err * err
                if abs(actual) > 1e-9:
                    ape[h] += abs(err) / abs(actual)
                    ape_cnt[h] += 1
                if res.lower[h] <= actual <= res.upper[h]:
                    hits[h] += 1
                counts[h] += 1
                width_sum += res.upper[h] - res.lower[h]
            origins += 1
        total = sum(counts)
        mae_h = [abs_err[h] / counts[h] for h in range(horizon)]
        reports.append(BacktestReport(
            name=name,
            horizon=horizon,
            n_origins=origins,
            mae=sum(abs_err) / total,
            rmse=math.sqrt(sum(sq_err) / total),
            mape=sum(ape) / max(sum(ape_cnt), 1),
            coverage=sum(hits) / total,
            mean_width=width_sum / total,
            per_horizon_coverage=[hits[h] / counts[h] for h in range(horizon)],
            per_horizon_mae=mae_h,
            warnings=warn_set,
        ))
    return reports
