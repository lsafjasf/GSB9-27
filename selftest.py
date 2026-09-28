"""自测与对拍：python3 selftest.py

覆盖：
  1. 手工推导序列对拍（q=0，观测 1,2,3，r=1，先验 x0=0,p0=1）
  2. q=0 在线 KF 与递推 WLS 逐步一致
  3. q>0 在线 KF 末点与离线批量最小二乘末点一致（含缺失）
  4. 噪声突变（已知 r 切换 / 自适应 r 两种策略）
  5. 长时间缺失（只预测、方差按 q 增长、恢复后收敛）
  6. 初值远离真值（连续拒绝 -> 先验膨胀自动解锁收敛）
  7. 离群门限规则边界断言（hard / soft / warmup）
  8. 数值稳定性长跑（1,000,000 步，方差恒正有限、稳态收敛）
  9. 综合场景：生成对拍与残差数据 residuals.csv（含离线平滑列）
"""

from __future__ import annotations

import csv
import math
import os
import random
import time

from kf1d import (
    ACCEPTED,
    MISSING,
    REJECTED,
    SOFT_OUTLIER,
    KalmanFilter1D,
    steady_state_p,
)
from reference import batch_smooth, wls_constant_online

PASS = "PASS"
FAILS = []


def check(name, cond, detail=""):
    tag = PASS if cond else "FAIL"
    print(f"  [{tag}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def mean(xs):
    return sum(xs) / len(xs)


def rmse(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / len(a))


# ---------------------------------------------------------------- 1. 手工序列
def test_hand_derived():
    print("test_hand_derived: q=0, z=[1,2,3], r=1, x0=0,p0=1")
    kf = KalmanFilter1D(x0=0.0, p0=1.0, q=0.0, r=1.0)
    res = kf.filter_sequence([1.0, 2.0, 3.0])
    # 手工：信息形式 精度 1+t，估计 = sum(z)/(1+t)
    expect_x = [1.0 / 2, 3.0 / 3, 6.0 / 4]
    expect_p = [1.0 / 2, 1.0 / 3, 1.0 / 4]
    expect_resid = [1.0, 1.5, 2.0]  # z - x_pred (第3步预测值为1.0)
    ok_x = all(abs(r.x - e) < 1e-12 for r, e in zip(res, expect_x))
    ok_p = all(abs(r.p - e) < 1e-12 for r, e in zip(res, expect_p))
    ok_r = all(abs(r.residual - e) < 1e-12 for r, e in zip(res, expect_resid))
    check("后验估计 == 手工序列 [0.5, 1.0, 1.5]", ok_x,
          f"got {[round(r.x,12) for r in res]}")
    check("后验方差 == 手工序列 [0.5, 1/3, 0.25]", ok_p,
          f"got {[r.p for r in res]}")
    check("残差 == [1, 1.5, 2]", ok_r, f"got {[r.residual for r in res]}")


# ------------------------------------------------ 2. q=0 与递推 WLS 逐步一致
def test_matches_wls_constant():
    print("test_matches_wls_constant: q=0 在线 KF vs 递推 WLS（含缺失）")
    rng = random.Random(7)
    n = 500
    zs = [rng.gauss(5.0, 2.0) for _ in range(n)]
    rs = [1.0 + 0.5 * (i % 3) for i in range(n)]
    for i in (3, 4, 100, 200, 201, 202, 499):
        zs[i] = None
    kf = KalmanFilter1D(x0=0.0, p0=10.0, q=0.0, r=1.0,
                        soft_nsigma=1e8, reject_nsigma=1e9)
    kres = kf.filter_sequence(zs, rs)
    wres = wls_constant_online(zs, rs, 0.0, 10.0)
    ok_x = all(abs(a.x - b[0]) < 1e-10 for a, b in zip(kres, wres))
    ok_p = all(abs(a.p - b[1]) < 1e-10 for a, b in zip(kres, wres))
    ok_r = all(
        (a.residual is None and b[2] is None)
        or (a.residual is not None and abs(a.residual - b[2]) < 1e-10)
        for a, b in zip(kres, wres)
    )
    check("逐步估计一致 (<1e-10)", ok_x)
    check("逐步方差一致 (<1e-10)", ok_p)
    check("逐步残差一致 (<1e-10)", ok_r)


# --------------------------------------- 3. q>0 末点 == 批量最小二乘末点
def test_matches_batch_endpoint():
    print("test_matches_batch_endpoint: q>0 KF 末点 vs 离线批量 MAP（含缺失）")
    rng = random.Random(11)
    n = 400
    x = 0.0
    zs, rs, truth = [], [], []
    q, r = 0.5, 2.0
    for i in range(n):
        x += rng.gauss(0.0, math.sqrt(q))
        z = None if i % 37 == 5 else x + rng.gauss(0.0, math.sqrt(r))
        zs.append(z)
        rs.append(r)
        truth.append(x)
    x0, p0 = 1.0, 5.0
    kf = KalmanFilter1D(x0=x0, p0=p0, q=q, r=r,
                        soft_nsigma=1e8, reject_nsigma=1e9)
    kres = kf.filter_sequence(zs, rs)
    xs_batch, _ = batch_smooth(zs, q, rs, x0, p0)
    ex = abs(kres[-1].x - xs_batch[-1])
    # 末点方差对拍：信息矩阵逆的末对角元。三对角信息阵前向消元到
    # 末点，Schur 补对角元的倒数即后验方差。
    inv_q = 1.0 / q
    d = 1.0 / p0 + inv_q + (1.0 / rs[0] if zs[0] is not None else 0.0)
    for k in range(1, n):
        a = -inv_q                    # 本行下次对角
        d_next = ((2.0 * inv_q) if k < n - 1 else inv_q) + (
            1.0 / rs[k] if zs[k] is not None else 0.0)
        d = d_next - (a * a) / d      # Schur 补
    p_batch_last = 1.0 / d
    ep = abs(kres[-1].p - p_batch_last)
    check(f"末点估计一致 (|dx|={ex:.2e})", ex < 1e-8)
    check(f"末点后验方差一致 (|dp|={ep:.2e})", ep < 1e-8)
    # 批量平滑（用全部数据）对真值应不差于纯滤波
    rmse_f = rmse([r.x for r in kres], truth)
    rmse_s = rmse(xs_batch, truth)
    check(f"平滑 RMSE {rmse_s:.3f} <= 滤波 RMSE {rmse_f:.3f}", rmse_s <= rmse_f + 1e-9)


# -------------------------------------------------------- 4. 噪声突变
def _rw(rng, n, q, r_schedule, x0=0.0, outliers=None, gaps=None):
    """生成随机游走真值与观测；r_schedule: callable(k)->r 或常数。"""
    gaps = gaps or set()
    outliers = outliers or {}
    x, truth, zs, rs = x0, [], [], []
    for k in range(n):
        x += rng.gauss(0.0, math.sqrt(q))
        r_k = r_schedule(k) if callable(r_schedule) else r_schedule
        if k in gaps:
            z = None
        else:
            z = x + rng.gauss(0.0, math.sqrt(r_k)) + outliers.get(k, 0.0)
        truth.append(x)
        zs.append(z)
        rs.append(r_k)
    return truth, zs, rs


def test_noise_jump():
    print("test_noise_jump: 观测噪声在 k=300 由 r=1 突变到 r=25")
    rng = random.Random(42)
    n = 600
    sched = lambda k: 1.0 if k < 300 else 25.0
    truth, zs, rs = _rw(rng, n, q=0.2, r_schedule=sched)

    # 策略 A：突变已知，逐帧传入正确 r
    kf_known = KalmanFilter1D(x0=truth[0], p0=1.0, q=0.2, r=1.0)
    a = kf_known.filter_sequence(zs, rs)
    # 策略 B：不告知，开自适应 r
    kf_adapt = KalmanFilter1D(x0=truth[0], p0=1.0, q=0.2, r=1.0,
                              adaptive_r=True, adaptive_alpha=0.1,
                              adaptive_after=20)
    b = kf_adapt.filter_sequence(zs)
    # 基线：不知道突变也不自适应（错误地相信小 r）
    kf_fixed = KalmanFilter1D(x0=truth[0], p0=1.0, q=0.2, r=1.0)
    c = kf_fixed.filter_sequence(zs)

    seg = slice(400, 600)
    e_known = rmse([r.x for r in a][seg], truth[400:600])
    e_adapt = rmse([r.x for r in b][seg], truth[400:600])
    e_fixed = rmse([r.x for r in c][seg], truth[400:600])
    print(f"    突变后 RMSE: 已知r={e_known:.3f} 自适应={e_adapt:.3f} "
          f"固定错误r={e_fixed:.3f}; 自适应终值 r_est={kf_adapt.r_est:.2f}")
    check("已知 r 切换的估计合理 (<2.0)", e_known < 2.0)
    check("自适应 RMSE 明显优于固定错误 r", e_adapt < 0.85 * e_fixed)
    check("自适应 r_est 已抬向新噪声水平 (>10)", kf_adapt.r_est > 10.0)
    # 突变前自适应不应把 r 抬得太离谱
    early = [r.residual for r in b[:300] if r.residual is not None]
    check("突变前估计仍贴合 (<1.0)",
          rmse([r.x for r in b[:300]], truth[:300]) < 1.0)


# -------------------------------------------------------- 5. 长时间缺失
def test_long_gap():
    print("test_long_gap: 200 步后连续缺失 100 步")
    rng = random.Random(5)
    n, gap = 400, range(200, 300)
    truth, zs, rs = _rw(rng, n, q=0.1, r_schedule=1.0, gaps=set(gap))
    kf = KalmanFilter1D(x0=truth[0], p0=1.0, q=0.1, r=1.0)
    res = kf.filter_sequence(zs, rs)
    all_missing = all(r.status == MISSING for r in res[200:300])
    check("缺失区间全部标注 missing", all_missing)
    # 缺失期：估计保持常数（随机游走预测），方差每步 +q
    p_growth_ok = all(abs(res[k].p - (res[199].p + (k - 199) * 0.1)) < 1e-9
                      for k in range(200, 300))
    check("缺失期方差按 P- = P + q 线性增长", p_growth_ok)
    x_flat_ok = all(res[k].x == res[199].x for k in range(200, 300))
    check("缺失期预测估计保持常数", x_flat_ok)
    # 恢复观测后 50 步内重新收敛
    after = rmse([r.x for r in res[300:350]], truth[300:350])
    check(f"恢复观测后 50 步内收敛 (RMSE={after:.3f} <1.2)", after < 1.2)
    # 不确定度要体现出来：缺口末方差显著大于缺口前
    check("缺口末不确定度显著增大", res[299].p > res[199].p + 5.0)


# ------------------------------------------------- 6. 初值远离真值
def test_far_initial():
    print("test_far_initial: x0=100, p0=1e-6，真值≈0；靠先验膨胀解锁")
    rng = random.Random(99)
    n = 300
    truth, zs, rs = _rw(rng, n, q=0.05, r_schedule=1.0, x0=0.0)
    kf = KalmanFilter1D(x0=100.0, p0=1e-6, q=0.05, r=1.0,
                        reject_inflate_after=3, inflate_max_trials=20,
                        p_inflate_max=1e4)
    res = kf.filter_sequence(zs, rs)
    n_rej = sum(r.status == REJECTED for r in res)
    n_infl = sum(1 for r in res if r.inflations > 0)
    check("前若干步观测被拒绝（锁住）", n_rej >= 2 and res[0].status == REJECTED)
    check("连续拒绝触发先验膨胀", n_infl >= 1)
    err50 = abs(res[50].x - truth[50])
    err_last = abs(res[-1].x - truth[-1])
    check(f"膨胀后自动解锁，k=50 误差 {err50:.3f} < 3", err50 < 3.0)
    check(f"末段收敛，误差 {err_last:.3f} < 1.5", err_last < 1.5)
    # 对照：不允许膨胀则会长时间锁死
    kf2 = KalmanFilter1D(x0=100.0, p0=1e-6, q=0.05, r=1.0,
                         inflate_max_trials=0, p_inflate_max=1e-6)
    res2 = kf2.filter_sequence(zs, rs)
    err50_locked = abs(res2[50].x - truth[50])
    check(f"无膨胀对照在 k=50 仍锁死 (误差 {err50_locked:.1f} > 20)",
          err50_locked > 20.0)


# --------------------------------------------- 7. 离群规则边界可断言
def test_outlier_rules():
    print("test_outlier_rules: 门限边界 / Huber 降权 / warmup")
    # p_pred=3, r=1 => innov_std=2；soft=2.5=>5.0，reject=4=>8.0
    kf = KalmanFilter1D(x0=0.0, p0=3.0, q=0.0, r=1.0,
                        soft_nsigma=2.5, reject_nsigma=4.0, gate_warmup=0)
    def one(z):
        k = KalmanFilter1D(x0=0.0, p0=3.0, q=0.0, r=1.0,
                           soft_nsigma=2.5, reject_nsigma=4.0)
        return k.step(z)

    r0 = one(0.0)
    r_near = one(4.5)    # d=2.25 < 2.5
    r_soft = one(6.0)    # d=3.0
    r_edge = one(7.99)   # d≈3.995 < 4
    r_hard = one(8.5)    # d=4.25 >= 4
    check("d<2.5: accepted, weight=1",
          r0.status == ACCEPTED and r_near.status == ACCEPTED
          and r0.weight == 1.0 and r_near.weight == 1.0)
    check("2.5<=d<4: soft_outlier 且 weight=2.5/d",
          r_soft.status == SOFT_OUTLIER and abs(r_soft.weight - 2.5 / 3.0) < 1e-12
          and r_edge.status == SOFT_OUTLIER)
    check("d>=4: rejected, weight=0, 只预测",
          r_hard.status == REJECTED and r_hard.weight == 0.0
          and r_hard.x == r_hard.x_pred and r_hard.p == r_hard.p_pred
          and r_hard.residual is None)
    # Huber 降权后增益缩小，估计移动幅度小于全权重情形
    check("soft 步移动方向正确且被缩减",
          (r_soft.x - 0.0) > 0 and (r_soft.x - 0.0) < (6.0 - 0.0))

    # warmup：前 gate_warmup 个观测不做门限
    kw = KalmanFilter1D(x0=0.0, p0=3.0, q=0.0, r=1.0, gate_warmup=2)
    s1 = kw.step(100.0)
    s2 = kw.step(100.0)
    s3 = kw.step(100.0)
    check("warmup 内离群也接受", s1.status == ACCEPTED and s2.status == ACCEPTED)
    check("warmup 后离群被拒绝", s3.status == REJECTED)

    # 含离群的整段：拒绝率与估计质量
    rng = random.Random(3)
    truth, zs, rs = _rw(rng, 300, q=0.1, r_schedule=1.0,
                        outliers={50: 20.0, 150: -25.0, 220: 30.0})
    kf3 = KalmanFilter1D(x0=truth[0], p0=1.0, q=0.1, r=1.0)
    rr = kf3.filter_sequence(zs, rs)
    rej_idx = {i for i, r in enumerate(rr) if r.status == REJECTED}
    check("注入的 3 个硬离群全部被拒绝", {50, 150, 220} <= rej_idx)
    err = rmse([r.x for r in rr], truth)
    check(f"含离群段总体 RMSE 小 ({err:.3f} < 1.3)", err < 1.3)


# ------------------------------------------------- 8. 数值稳定性长跑
def test_stability():
    print("test_stability: 1,000,000 步长跑（含缺失与极端初值）")
    kf = KalmanFilter1D(x0=0.0, p0=1e-12, q=0.01, r=1.0, p_floor=1e-12)
    rng = random.Random(2024)
    t0 = time.time()
    min_p, max_p = math.inf, 0.0
    n_missing = 0
    for k in range(1_000_000):
        z = None if k % 1000 == 7 else rng.gauss(0.0, 1.0)
        r = kf.step(z)
        if not (math.isfinite(r.x) and math.isfinite(r.p)):
            check("全部 x,p 有限", False)
            return
        if r.p <= 0.0:
            check("方差严格为正", False, f"k={k}, p={r.p}")
            return
        if z is None:
            n_missing += 1
        min_p = min(min_p, r.p)
        max_p = max(max_p, r.p)
    dt = time.time() - t0
    check("1e6 步全部 x,p 有限", True)
    check(f"方差始终 > 0（最小 {min_p:.3e}，下限 1e-12）", min_p >= 1e-12)
    # q=0.01,r=1 的稳态先验方差约 0.105；后验约 0.095
    ss_post = steady_state_p(0.01, 1.0)
    ss_post = ss_post * 1.0 / (ss_post + 1.0)  # 后验 P = P- R/(P-+R)
    check(f"长期均值方差接近稳态 {ss_post:.4f}（末 p={kf.p:.4f}）",
          abs(kf.p - ss_post) < 0.02)
    print(f"    耗时 {dt:.2f}s，缺失 {n_missing} 步，p 范围 [{min_p:.3e},{max_p:.3e}]")

    # 极端：q=0 且持续观测，方差单调趋于下限但不归零
    kf0 = KalmanFilter1D(x0=0.0, p0=1.0, q=0.0, r=1.0, p_floor=1e-9)
    ps = [kf0.step(1.0).p for _ in range(10000)]
    check("q=0 时方差单调下降但被下限托底",
          all(ps[i + 1] <= ps[i] + 1e-15 for i in range(9999)) and ps[-1] >= 1e-9)


# ----------------------- 9. 综合场景 + 对拍/残差数据 residuals.csv
def test_scenario_and_csv(csv_path):
    print("test_scenario_and_csv: 综合场景并导出 residuals.csv")
    rng = random.Random(2026)
    n = 500
    q = 0.1
    gaps = set(range(120, 180))                      # 60 步长缺失
    noise_sched = lambda k: 1.0 if k < 300 else 16.0  # 后半段噪声突变
    outliers = {40: 18.0, 250: -22.0, 400: 20.0}      # 硬离群
    truth, zs, rs_true = _rw(rng, n, q, noise_sched, x0=0.0,
                             outliers=outliers, gaps=gaps)

    kf = KalmanFilter1D(x0=50.0, p0=1e4, q=q, r=1.0,
                        soft_nsigma=2.5, reject_nsigma=4.0,
                        adaptive_r=True, adaptive_alpha=0.1, adaptive_after=20)
    res = kf.filter_sequence(zs)

    # 离线参照：批量平滑使用真实 r 与全部观测（离群也在其中，作为对照）
    xs_batch, _ = batch_smooth(zs, q, rs_true, 50.0, 1e4)

    # 断言：3 个注入离群都不能全权重通过（拒绝或 Huber 降权）
    for i_out in (40, 250, 400):
        assert res[i_out].weight < 1.0, f"k={i_out} 离群未被抑制"
    assert {250, 400} <= {i for i, r in enumerate(res) if r.status == REJECTED}
    assert all(res[i].status == MISSING for i in gaps)
    err_end = rmse([r.x for r in res[420:]], truth[420:])
    check(f"噪声突变+离群+长缺失综合场景末段 RMSE={err_end:.3f} (<3)",
          err_end < 3.0)
    check("批量平滑末段同样贴合真值",
          rmse(xs_batch[420:], truth[420:]) < 3.0)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "k", "truth", "observation", "obs_missing", "status",
            "x_pred", "p_pred", "x_filtered", "p_filtered",
            "x_smoothed_batch", "residual_innovation",
            "normalized_residual", "weight", "r_eff", "inflations",
        ])
        for i, r in enumerate(res):
            w.writerow([
                i, f"{truth[i]:.6f}",
                "" if zs[i] is None else f"{zs[i]:.6f}",
                int(zs[i] is None), r.status,
                f"{r.x_pred:.6f}", f"{r.p_pred:.6f}",
                f"{r.x:.6f}", f"{r.p:.6f}", f"{xs_batch[i]:.6f}",
                "" if r.residual is None else f"{r.residual:.6f}",
                "" if r.normalized_residual is None
                else f"{r.normalized_residual:.6f}",
                f"{r.weight:.4f}",
                "" if r.r_eff is None else f"{r.r_eff:.6f}",
                r.inflations,
            ])
    check(f"残差/对拍数据已写出 {os.path.basename(csv_path)} (500 行)",
          os.path.getsize(csv_path) > 0)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    tests = [
        test_hand_derived,
        test_matches_wls_constant,
        test_matches_batch_endpoint,
        test_noise_jump,
        test_long_gap,
        test_far_initial,
        test_outlier_rules,
        test_stability,
    ]
    t0 = time.time()
    for t in tests:
        t()
    csv_path = os.path.join(here, "residuals.csv")
    test_scenario_and_csv(csv_path)
    print("-" * 64)
    if FAILS:
        print(f"{len(FAILS)} 项失败: {FAILS}")
        raise SystemExit(1)
    print(f"全部断言通过，总耗时 {time.time() - t0:.2f}s")
    print(f"对拍与残差数据: {csv_path}")


if __name__ == "__main__":
    main()
