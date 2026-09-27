"""tsdecompose: 自动周期估计 + 趋势/周期/残差分解（仅标准库）。

用法:
    from tsdecompose import decompose, estimate_period
    result = decompose(series)   # series 中缺失点用 None 或 float('nan') 表示
    result.period     # 估计出的主周期（无法估计时为 None）
    result.trend      # 趋势分量（与输入等长，缺失位置已插值）
    result.seasonal   # 周期分量（与输入等长）
    result.residual   # 残差（输入缺失的位置为 None）
    result.mode       # 分解模式，见 README「降级行为」
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

__all__ = ["estimate_period", "decompose", "Decomposition"]

_EPS = 1e-12


def _normalize(series: Sequence) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for v in series:
        if v is None:
            out.append(None)
            continue
        f = float(v)
        out.append(None if math.isnan(f) else f)
    return out


def _odd(n: int) -> int:
    n = max(1, int(n))
    return n if n % 2 == 1 else n + 1


def _moving_average(values: List[Optional[float]], window: int,
                    min_count: Optional[int] = None) -> List[Optional[float]]:
    """中心滑动平均，容忍缺失；窗口内有效点不足 min_count 时输出 None。"""
    n = len(values)
    window = max(1, min(window, n))
    half = window // 2
    if min_count is None:
        min_count = max(2, window // 3)
    out: List[Optional[float]] = [None] * n
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i - half + window)
        total = 0.0
        count = 0
        for j in range(lo, hi):
            v = values[j]
            if v is not None:
                total += v
                count += 1
        if count >= min_count:
            out[i] = total / count
    return out


def _interpolate(values: List[Optional[float]]) -> List[Optional[float]]:
    """线性插值填补内部空洞，两端用最近有效值外延。"""
    n = len(values)
    known = [i for i, v in enumerate(values) if v is not None]
    if not known:
        return [None] * n
    out: List[Optional[float]] = list(values)
    for i in range(known[0]):
        out[i] = values[known[0]]
    for i in range(known[-1] + 1, n):
        out[i] = values[known[-1]]
    for a, b in zip(known, known[1:]):
        va, vb = values[a], values[b]
        span = b - a
        for i in range(a + 1, b):
            t = (i - a) / span
            out[i] = va * (1.0 - t) + vb * t
    return out


def _detrend_for_acf(values: List[Optional[float]]) -> List[Optional[float]]:
    """ACF 前的预去趋势：用较宽滑动平均移除慢变成分（含趋势突变）。"""
    n_valid = sum(1 for v in values if v is not None)
    window = _odd(max(5, n_valid // 5))
    trend = _interpolate(_moving_average(values, window))
    return [None if (v is None or t is None) else v - t
            for v, t in zip(values, trend)]


def _autocorrelation(values: List[Optional[float]],
                     max_lag: int) -> Optional[List[Optional[float]]]:
    """缺失感知的自相关系数，lag 0..max_lag；有效配对不足时提前截断。"""
    n = len(values)
    valid = [v for v in values if v is not None]
    if len(valid) < 2:
        return None
    mean = sum(valid) / len(valid)
    centered = [None if v is None else v - mean for v in values]
    var = sum(c * c for c in centered if c is not None)
    if var <= _EPS:
        return None
    acf: List[Optional[float]] = [None] * (max_lag + 1)
    acf[0] = 1.0
    for lag in range(1, max_lag + 1):
        total = 0.0
        count = 0
        for i in range(n - lag):
            a = centered[i]
            b = centered[i + lag]
            if a is not None and b is not None:
                total += a * b
                count += 1
        if count < max(6, (n - lag) // 4):
            break
        acf[lag] = total / var
    return acf


def estimate_period(series: Sequence,
                    min_period: int = 2,
                    max_period: Optional[int] = None,
                    acf_threshold: float = 0.3) -> Optional[int]:
    """用 ACF 第一个显著峰估计主周期；估计不出（常数/纯噪声/太短）返回 None。"""
    values = _normalize(series)
    n = len(values)
    valid = [v for v in values if v is not None]
    if len(valid) < 8:
        return None
    if max(valid) - min(valid) <= _EPS:  # 常数序列
        return None
    if max_period is None:
        max_period = n // 2
    max_period = min(max_period, n - 2)
    if max_period < max(1, min_period):
        return None
    detrended = _detrend_for_acf(values)
    acf = _autocorrelation(detrended, max_period + 1)
    if acf is None:
        return None
    peaks: List[int] = []
    for lag in range(max(1, min_period), max_period + 1):
        cur, left, right = acf[lag], acf[lag - 1], acf[lag + 1]
        if cur is None or left is None or right is None:
            break
        if cur >= acf_threshold and cur >= left and cur > right:
            peaks.append(lag)
    if not peaks:
        return None
    best = max(peaks, key=lambda l: acf[l])
    cutoff = 0.6 * acf[best]
    for lag in peaks:  # 取第一个足够强的峰，避免选到谐波（2p, 3p...）
        if acf[lag] >= cutoff:
            return lag
    return None


@dataclass
class Decomposition:
    period: Optional[int]
    trend: List[Optional[float]]
    seasonal: List[Optional[float]]
    residual: List[Optional[float]]
    mode: str  # full / trend_only / short_for_period / insufficient_data / empty

    def reconstructed(self) -> List[Optional[float]]:
        out: List[Optional[float]] = []
        for t, s, r in zip(self.trend, self.seasonal, self.residual):
            if t is None or s is None or r is None:
                out.append(None)
            else:
                out.append(t + s + r)
        return out


def _trend_only(values, period, mode):
    n_valid = sum(1 for v in values if v is not None)
    window = _odd(max(3, n_valid // 5))
    trend = _interpolate(_moving_average(values, window))
    seasonal = [0.0] * len(values)
    residual = [None if v is None else v - t
                for v, t in zip(values, trend)]
    return Decomposition(period, trend, seasonal, residual, mode)


def decompose(series: Sequence,
              period: Optional[int] = None,
              min_period: int = 2,
              max_period: Optional[int] = None) -> Decomposition:
    """趋势/周期/残差分解。period 可不传（自动估计）；显式传入则跳过估计。"""
    values = _normalize(series)
    n = len(values)
    n_valid = sum(1 for v in values if v is not None)
    none_col: List[Optional[float]] = [None] * n

    if n_valid == 0:
        return Decomposition(None, none_col, list(none_col), list(none_col), "empty")

    if n_valid < 6:
        trend = _interpolate(values)
        seasonal = [0.0] * n
        residual = [0.0 if v is not None else None for v in values]
        return Decomposition(None, trend, seasonal, residual, "insufficient_data")

    if period is None:
        period = estimate_period(values, min_period=min_period, max_period=max_period)

    if period is None:
        return _trend_only(values, None, "trend_only")
    if period < 2 or n_valid < 2 * period:
        return _trend_only(values, period, "short_for_period")

    window = period if period % 2 == 1 else period + 1
    if window > n:
        return _trend_only(values, period, "short_for_period")
    trend = _interpolate(_moving_average(values, window))

    detrended = [None if (v is None or t is None) else v - t
                 for v, t in zip(values, trend)]
    phase_sum = [0.0] * period
    phase_cnt = [0] * period
    for i, d in enumerate(detrended):
        if d is not None:
            k = i % period
            phase_sum[k] += d
            phase_cnt[k] += 1
    template = [phase_sum[k] / phase_cnt[k] if phase_cnt[k] else 0.0
                for k in range(period)]
    offset = sum(template) / period
    template = [x - offset for x in template]
    seasonal = [template[i % period] for i in range(n)]

    residual = [None if v is None else v - t - s
                for v, t, s in zip(values, trend, seasonal)]
    return Decomposition(period, trend, seasonal, residual, "full")
