"""一维 Kalman 滤波（局部水平 / 随机游走模型），仅依赖标准库。

模型设定
--------
状态: 标量 x（被估计的物理量，如位置、温度、液位等）
状态方程: x_k = x_{k-1} + w_k,  w_k ~ N(0, q)
观测方程: z_k = x_k + v_k,      v_k ~ N(0, r_k)

参数设定方式
------------
- x0, p0 : 初始状态与其方差。初值不确定时把 p0 设大（如 1e6），滤波会快速收敛。
- q      : 过程噪声方差，刻画真值每步漂移量。q 大 -> 跟踪快但平滑差；q 小 -> 平滑好但滞后。
- r      : 观测噪声方差，可由传感器手册或离线数据残差方差估计。支持每步覆盖（噪声突变场景）。
- gate   : 离群判定门限，对标准化残差平方 NIS = r_k^2 / S_k 生效，默认 9.0（约 3 sigma）。
- outlier_strategy: "inflate"（按 NIS 放大 R，降低权重，默认）或 "reject"（整点拒绝）。
- p_min  : 方差下限保护，防止长期运行后 P 被舍入成 0 或负数。

数值稳定措施
------------
1. 协方差更新使用 Joseph 形式: P = (1-K)^2 P + K^2 R，对舍入误差保证非负。
2. 每步对 P 做下限截断 max(P, p_min)，并对称性在标量情形天然成立。
3. 新息方差 S 同样有下限保护，避免除零。
"""

from dataclasses import dataclass
from typing import Optional

P_MIN_DEFAULT = 1e-12
S_MIN = 1e-300


@dataclass
class StepResult:
    """单步滤波输出。"""
    k: int                    # 步序号（从 0 开始）
    estimate: float           # 状态估计 x
    variance: float           # 估计方差 P
    residual: Optional[float] # 新息（残差）z - x_pred；缺测或拒绝时为 None
    residual_var: Optional[float]  # 新息方差 S
    nis: Optional[float]      # 标准化残差平方 r^2 / S
    missing: bool             # 本步观测缺失，仅做了预测
    outlier: bool             # 本步观测被判为离群
    weight: float             # 实际权重系数 R/R_eff：1.0 正常，(0,1) 降权，0.0 拒绝/缺测


class Kalman1D:
    def __init__(self, x0, p0, q, r, gate=9.0,
                 outlier_strategy="inflate", p_min=P_MIN_DEFAULT):
        if p0 <= 0 or q < 0 or r <= 0:
            raise ValueError("要求 p0>0, q>=0, r>0")
        if outlier_strategy not in ("inflate", "reject"):
            raise ValueError("outlier_strategy 必须是 'inflate' 或 'reject'")
        self.x = float(x0)
        self.P = float(p0)
        self.q = float(q)
        self.r = float(r)
        self.gate = float(gate)
        self.outlier_strategy = outlier_strategy
        self.p_min = float(p_min)
        self.k = -1  # 已处理的步数

    def predict(self, q=None):
        """预测步：x 不变，P += q。"""
        self.P = self.P + (self.q if q is None else float(q))
        if self.P < self.p_min:
            self.P = self.p_min

    def update(self, z, r=None):
        """更新步。返回 (residual, S, nis, outlier, weight)。"""
        R = self.r if r is None else float(r)
        if R <= 0:
            raise ValueError("观测噪声方差必须为正")
        residual = float(z) - self.x
        S = self.P + R
        if S < S_MIN:
            S = S_MIN
        nis = residual * residual / S
        outlier = nis > self.gate
        if outlier and self.outlier_strategy == "reject":
            return residual, S, nis, True, 0.0
        R_eff = R * (nis / self.gate) if outlier else R  # inflate: 降权
        K = self.P / (self.P + R_eff)
        self.x = self.x + K * residual
        # Joseph 形式，保证非负
        one_minus_K = 1.0 - K
        self.P = one_minus_K * one_minus_K * self.P + K * K * R_eff
        if self.P < self.p_min:
            self.P = self.p_min
        return residual, S, nis, outlier, R / R_eff

    def step(self, z, r=None, q=None):
        """完整一步：预测 + （若有观测）更新。z 为 None 表示缺测。"""
        self.k += 1
        self.predict(q=q)
        if z is None:
            return StepResult(self.k, self.x, self.P, None, None, None,
                              True, False, 0.0)
        residual, S, nis, outlier, weight = self.update(z, r=r)
        if outlier and weight == 0.0:  # reject 策略：观测被丢弃
            return StepResult(self.k, self.x, self.P, None, None, nis,
                              False, True, 0.0)
        return StepResult(self.k, self.x, self.P, residual, S, nis,
                          False, outlier, weight)
