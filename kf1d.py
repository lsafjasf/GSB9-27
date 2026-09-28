"""一维状态估计库：带缺失/离群处理与自适应噪声的标量卡尔曼滤波。

模型（一维随机游走 / 局部恒定状态）
----------------------------------
    状态方程:  x[k] = x[k-1] + w[k],  w[k] ~ N(0, q)   （过程噪声）
    观测方程:  z[k] = x[k]   + v[k],  v[k] ~ N(0, r)   （观测噪声）

参数含义与设定方式
    x0, p0 : 初始状态先验及其方差。
             初值未知时取一个合理的中心点，p0 取大（如 1e2~1e6），
             滤波器会在若干步后“忘掉”先验；p0 不得为负。
    q      : 过程噪声方差（process variance），描述信号本身每步允许
             变化多少。设定方式：
               * 已知物理模型直接填；
               * 用信号相邻差分方差的经验估计 q ≈ Var(diff(x))；
               * 或用稳态形式调参（见 README）。
             q 越大跟踪越快但平滑越弱；q=0 退化为常数模型。
    r      : 观测噪声方差（measurement variance）。
             可用静态段样本方差估计；若噪声水平会突变，可开启
             adaptive_r 在线估计（指数加权方差）。

每一步输出（见 UpdateResult）：
    x, p   : 更新后的状态估计与估计方差（>= p_floor，保证非负）。
    residual: 新息/残差 z - x_pred；缺失或被拒时为 None。
    status : accepted / soft_outlier / rejected / missing。

仅依赖 Python 3.8+ 标准库。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

ACCEPTED = "accepted"
SOFT_OUTLIER = "soft_outlier"
REJECTED = "rejected"
MISSING = "missing"


@dataclass
class UpdateResult:
    """单步递推结果。"""

    k: int                 # 已执行的步数（predict 次数，从 1 开始）
    x: float               # 状态估计
    p: float               # 估计方差（后验；missing/rejected 时为先验预测方差）
    x_pred: float          # 预测状态
    p_pred: float          # 预测（先验）方差
    residual: Optional[float]   # 新息 z - x_pred；缺失/拒绝时为 None
    innov_var: Optional[float]  # 新息方差 p_pred + r_eff；缺失时为 None
    normalized_residual: Optional[float]  # (z - x_pred)/sqrt(innov_var)
    weight: float          # 实际观测权重因子（1=全权重，0=未采用，(0,1)=降权）
    r_eff: Optional[float] # 本步实际使用的观测噪声方差（自适应/降权后）
    status: str            # accepted / soft_outlier / rejected / missing
    inflations: int        # 本步因连续拒绝而做的先验方差膨胀次数


class KalmanFilter1D:
    """标量卡尔曼滤波器（随机游走模型）。

    离群处理规则（可断言，见 selftest）
        令 d = |z - x_pred| / sqrt(p_pred + r_eff) 为标准化新息。
        1. d >= reject_nsigma（默认 4.0）：拒绝，本步只做预测；
           连续拒绝达到 reject_inflate_after（默认 3）次时，先把 p_pred
           翻倍（上限 p_inflate_max）再重新判定，最多 inflate_max_trials 次。
           这保证“初值远离真值”时滤波器能自动解锁，而不是永久锁死。
        2. soft_nsigma（默认 2.5）<= d < reject_nsigma：软离群，
           按 Huber 权重 w = soft_nsigma / d 把有效观测方差放大为
           r_eff / w^2（等价于增益乘以 w），不拒绝。
        3. d < soft_nsigma：正常全权重更新。
        4. 前 gate_warmup 个到齐的观测不做门限（用于初值快速收敛；
           默认 0，靠先验膨胀机制即可解锁）。

    数值稳定性
        * 预测方差直接加 q，再对称化与下限保护；
        * 更新方差使用 Joseph 形式 P = (1-K)^2 P- + K^2 R_eff，
          该式由两个非负项构成，不会产生负方差；
        * 再做对称化（标量即 (p+p)/2）与下限 max(p, p_floor)。
    """

    def __init__(
        self,
        x0: float = 0.0,
        p0: float = 1.0,
        q: float = 1.0,
        r: float = 1.0,
        *,
        soft_nsigma: float = 2.5,
        reject_nsigma: float = 4.0,
        reject_inflate_after: int = 3,
        inflate_max_trials: int = 10,
        p_inflate_max: float = 60.0,
        gate_warmup: int = 0,
        adaptive_r: bool = False,
        adaptive_alpha: float = 0.1,
        adaptive_after: int = 20,
        p_floor: float = 1e-12,
    ) -> None:
        if p0 < 0.0 or q < 0.0 or r < 0.0:
            raise ValueError("p0, q, r 必须非负")
        if soft_nsigma <= 0.0 or reject_nsigma <= soft_nsigma:
            raise ValueError("要求 0 < soft_nsigma < reject_nsigma")
        if reject_inflate_after < 1 or inflate_max_trials < 0:
            raise ValueError("拒绝膨胀次数参数非法")
        if not (0.0 < adaptive_alpha <= 1.0):
            raise ValueError("adaptive_alpha 必须在 (0, 1]")
        if p_floor <= 0.0:
            raise ValueError("p_floor 必须为正数")

        self.x = float(x0)
        self.p = self._guard(float(p0), p_floor)
        self.q = float(q)
        self.r_nominal = float(r)
        self.r_est = float(r)   # 在线（EWMA）估计的观测方差
        self.soft_nsigma = float(soft_nsigma)
        self.reject_nsigma = float(reject_nsigma)
        self.reject_inflate_after = int(reject_inflate_after)
        self.inflate_max_trials = int(inflate_max_trials)
        self.p_inflate_max = float(p_inflate_max)
        self.gate_warmup = int(gate_warmup)
        self.adaptive_r = bool(adaptive_r)
        self.adaptive_alpha = float(adaptive_alpha)
        self.adaptive_after = int(adaptive_after)
        self.p_floor = float(p_floor)

        self.k = 0                 # 已预测步数
        self.obs_count = 0         # 到齐（非缺失）观测数
        self.accepted_count = 0    # 被采用（含降权）观测数
        self.reject_streak = 0     # 连续拒绝计数（缺失不重置）

    @staticmethod
    def _guard(p: float, p_floor: float) -> float:
        """对称化 + 有限性检查 + 方差下限保护。"""
        if not math.isfinite(p):
            raise FloatingPointError("协方差出现非有限值")
        p = 0.5 * (p + p)  # 标量对称化（与矩阵 (P+P^T)/2 对应）
        if p < p_floor:
            p = p_floor
        return p

    def _effective_r(self) -> float:
        """自适应开启且观测数足够后，用在线估计放大名义噪声下限。"""
        if self.adaptive_r and self.obs_count >= self.adaptive_after:
            return max(self.r_nominal, self.r_est)
        return self.r_nominal

    def predict(self) -> Tuple[float, float]:
        """预测步：x- = x, P- = P + q（随机游走，F=1）。"""
        self.k += 1
        self.x = self.x
        self.p = self._guard(self.p + self.q, self.p_floor)
        return self.x, self.p

    def update(
        self, z: Optional[float], r: Optional[float] = None
    ) -> UpdateResult:
        """一个完整的“预测 + 更新”递推步。

        z 为 None / NaN 表示观测缺失：只做预测并标注 missing。
        r 可在本步临时覆盖观测噪声方差（用于已知噪声水平突变的场景）。
        """
        # 无论观测是否缺失都先预测
        self.predict()
        x_pred, p_pred = self.x, self.p

        # ---- 缺失：只预测 ----
        if z is None or (isinstance(z, float) and math.isnan(z)):
            return UpdateResult(
                self.k, self.x, self.p, x_pred, p_pred,
                None, None, None, 0.0, None, MISSING, 0,
            )

        self.obs_count += 1
        r_eff_base = float(r) if r is not None else self._effective_r()
        if r_eff_base < 0.0:
            raise ValueError("观测方差 r 必须非负")

        innov = z - x_pred
        innov_var = p_pred + r_eff_base
        d = abs(innov) / math.sqrt(max(innov_var, self.p_floor))

        # ---- 门限判定（带先验膨胀解锁） ----
        gated = self.obs_count > self.gate_warmup
        inflations = 0
        rejected = gated and d >= self.reject_nsigma
        while (
            rejected
            and self.reject_streak + 1 >= self.reject_inflate_after
            and inflations < self.inflate_max_trials
            and p_pred < self.p_inflate_max
        ):
            # 连续拒绝 => 可能是模型/初值错了，膨胀先验重新尝试
            p_pred = self._guard(2.0 * p_pred, self.p_floor)
            inflations += 1
            innov_var = p_pred + r_eff_base
            d = abs(innov) / math.sqrt(max(innov_var, self.p_floor))
            rejected = d >= self.reject_nsigma

        if gated and rejected:
            self.reject_streak += 1
            self.x, self.p = x_pred, self._guard(p_pred, self.p_floor)
            return UpdateResult(
                self.k, self.x, self.p, x_pred, p_pred,
                None, innov_var, d, 0.0, r_eff_base, REJECTED, inflations,
            )

        # ---- 软离群：Huber 降权 ----
        if gated and d >= self.soft_nsigma:
            weight = self.soft_nsigma / d  # (0, 1)
            status = SOFT_OUTLIER
        else:
            weight = 1.0
            status = ACCEPTED

        r_eff = r_eff_base / (weight * weight)
        innov_var_eff = p_pred + r_eff
        gain = p_pred / max(innov_var_eff, self.p_floor)

        self.x = x_pred + gain * innov
        # Joseph 形式（F=1）：两个非负项相加，结构上保证方差非负
        p_new = (1.0 - gain) ** 2 * p_pred + gain ** 2 * r_eff
        self.p = self._guard(p_new, self.p_floor)
        self.reject_streak = 0
        self.accepted_count += 1

        # ---- 在线观测噪声自适应（EWMA；用 Huber 截断的新息防离群污染）----
        if self.adaptive_r and r is None:
            capped = min(abs(innov), self.soft_nsigma * math.sqrt(p_pred + self.r_est))
            sample_var = capped * capped
            if self.obs_count == 1:
                self.r_est = sample_var
            else:
                self.r_est = (
                    (1.0 - self.adaptive_alpha) * self.r_est
                    + self.adaptive_alpha * sample_var
                )

        return UpdateResult(
            self.k, self.x, self.p, x_pred, p_pred,
            innov, innov_var_eff, d, weight, r_eff, status, inflations,
        )

    def step(self, z: Optional[float], r: Optional[float] = None) -> UpdateResult:
        """update 的别名，语义即“走一步”。"""
        return self.update(z, r)

    def filter_sequence(
        self, zs: Sequence[Optional[float]], rs: Optional[Sequence[Optional[float]]] = None
    ) -> List[UpdateResult]:
        """对整段观测（None/NaN 表示缺失）做递推，返回每步结果。"""
        out: List[UpdateResult] = []
        for i, z in enumerate(zs):
            r_i = rs[i] if rs is not None else None
            out.append(self.update(z, r_i))
        return out


def steady_state_p(q: float, r: float) -> float:
    """随机游走标量 KF 的稳态先验方差解析值。

    P- 满足 P-^2 - q P- - q r = 0（Riccati 方程不动点），
    P- = (q + sqrt(q^2 + 4 q r)) / 2。供测试与调参参考。
    """
    if q < 0.0 or r < 0.0:
        raise ValueError("q, r 必须非负")
    return 0.5 * (q + math.sqrt(q * q + 4.0 * q * r))
