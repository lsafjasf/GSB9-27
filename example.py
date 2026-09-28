"""最小使用示例：python3 example.py"""
from kf1d import KalmanFilter1D

# 信号每步缓慢游走(q=0.05)，观测噪声方差 r=1；
# 初值未知 -> x0 给 0，p0 给大一点让滤波器快速接受数据。
kf = KalmanFilter1D(x0=0.0, p0=10.0, q=0.05, r=1.0,
                    soft_nsigma=2.5, reject_nsigma=4.0)

stream = [1.05, 0.97, 1.10, None, 18.0, 1.02, 0.99]  # None=缺失, 18=离群
for k, z in enumerate(stream):
    r = kf.step(z)  # 一步完成 预测+更新；也可分开 kf.predict()/kf.update(z)
    if r.status == "missing":
        note = "缺失，仅预测"
    elif r.status == "rejected":
        note = f"离群拒绝(d={r.normalized_residual:.1f})，仅预测"
    elif r.status == "soft_outlier":
        note = f"软离群降权(w={r.weight:.2f})"
    else:
        note = f"残差={r.residual:+.3f}"
    print(f"k={k} z={str(z):>5} x={r.x:6.3f} P={r.p:6.3f} {note}")
