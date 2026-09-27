"""test_fftconv — fftconv 的自测、误差分析与耗时对比。

运行：python3 test_fftconv.py
"""

import random
import time

from fftconv import convolve, naive_convolve, fft, ifft, next_pow2

random.seed(20260928)


def rand_seq(n, lo=-1.0, hi=1.0):
    return [random.uniform(lo, hi) for _ in range(n)]


def max_err(x, y):
    """返回 (最大绝对误差, 相对误差(相对结果最大幅值))。"""
    assert len(x) == len(y), (len(x), len(y))
    peak = max((abs(v) for v in y), default=0.0) or 1.0
    mabs = max((abs(p - q) for p, q in zip(x, y)), default=0.0)
    return mabs, mabs / peak


def check(name, a, b, tol=1e-8):
    got = convolve(a, b)
    ref = naive_convolve(a, b)
    mabs, mrel = max_err(got, ref)
    status = "OK " if mrel <= tol else "FAIL"
    print("  [%s] %-34s len=%5d+%-5d max_abs=%.3e max_rel=%.3e"
          % (status, name, len(a), len(b), mabs, mrel))
    assert mrel <= tol, "用例失败: %s" % name


def test_fft_roundtrip():
    print("== FFT 正逆变换往返 ==")
    for n in (1, 2, 4, 8, 64, 1024, 4096):
        x = rand_seq(n)
        back = ifft(fft(x))
        err = max(abs(p - q) for p, q in zip(x, back))
        print("  n=%5d  roundtrip max_err=%.3e" % (n, err))
        assert err < 1e-9
    try:
        fft([1.0, 2.0, 3.0])
        raise AssertionError("非二次幂长度应抛异常")
    except ValueError:
        print("  非二次幂长度正确抛出 ValueError（由 convolve 负责补零）")


def test_correctness():
    print("== 与朴素卷积对拍（含非二次幂长度）==")
    for n, m in [(1, 1), (2, 3), (5, 7), (16, 16), (17, 31), (48, 49),
                 (63, 65), (100, 1), (1, 100), (128, 200), (255, 257),
                 (300, 500), (1000, 999), (1024, 1024), (2000, 1500)]:
        check("随机均匀 n=%d m=%d" % (n, m), rand_seq(n), rand_seq(m), tol=1e-8)


def test_edge_cases():
    print("== 边界与极端输入 ==")
    check("全零 x 随机", [0.0] * 500, rand_seq(300))
    check("随机 x 全零", rand_seq(300), [0.0] * 500)
    check("全零 x 全零", [0.0] * 100, [0.0] * 100)
    check("单点 x 单点", [3.5], [-2.0])
    check("单点 x 长序列", [2.5], rand_seq(1000))
    check("冲激 x 随机", [1.0] + [0.0] * 999, rand_seq(777))
    check("常数序列", [1.0] * 1000, [1.0] * 1000, tol=1e-7)
    check("交替符号", [1.0, -1.0] * 500, rand_seq(600), tol=1e-8)

    # 极小幅值：1e-160 量级，真实卷积结果 ~1e-318，落在 float64
    # 次正规（subnormal）区。归一化保证中间量保持在 O(1)~O(n)，
    # 仅在最后一步落入次正规区，不会因下溢整段变零。
    # 用 Decimal 高精度基准验证（此时朴素 float64 卷积自身也有
    # 每次乘积的次正规舍入，不宜作基准）。
    from decimal import Decimal, getcontext
    getcontext().prec = 60
    a = rand_seq(200, 0.5e-160, 1e-160)
    b = rand_seq(200, 0.5e-160, 1e-160)
    got = convolve(a, b)
    da = [Decimal(v) for v in a]
    db = [Decimal(v) for v in b]
    ref = []
    for k in range(len(a) + len(b) - 1):
        lo = max(0, k - len(b) + 1)
        hi = min(k, len(a) - 1)
        ref.append(sum((da[i] * db[k - i] for i in range(lo, hi + 1)),
                       Decimal(0)))
    peak = max(abs(v) for v in ref)
    worst = max(abs(Decimal(g) - r) for g, r in zip(got, ref)) / peak
    ok = peak > 0 and worst < 1e-5
    print("  [%s] 极小幅值(~1e-160,结果在次正规区) peak=%.3e rel_vs_Decimal=%.3e"
          % ("OK " if ok else "FAIL", float(peak), float(worst)))
    assert ok

    # 极大幅值：1e150 量级
    check("极大幅值(~1e150)", rand_seq(300, -1e150, 1e150),
          rand_seq(300, -1e150, 1e150), tol=1e-8)

    # 同一序列内幅度差异极大
    a = [1e150 if i % 2 else 1e-150 for i in range(400)]
    b = rand_seq(300)
    check("序列内幅度差 1e300", a, b, tol=1e-8)

    # 两条序列之间幅度差异极大：1e-150 与 1e150
    check("序列间幅度差 1e300", rand_seq(300, -1e-150, 1e-150),
          rand_seq(300, -1e150, 1e150), tol=1e-8)

    # 复数输入
    ca = [complex(random.uniform(-1, 1), random.uniform(-1, 1))
          for _ in range(300)]
    cb = [complex(random.uniform(-1, 1), random.uniform(-1, 1))
          for _ in range(200)]
    check("复数输入", ca, cb, tol=1e-8)


def test_long_sequence():
    print("== 超长序列 ==")
    n, m = 200_000, 150_001  # 非二次幂
    # 闭式解：ones(n) * ones(m) 是三角波，第 i 项 = min(i+1, n, m, n+m-1-i)
    got = convolve([1.0] * n, [1.0] * m)
    ref_peak = min(n, m)
    mabs = 0.0
    for i, v in enumerate(got):
        expect = min(i + 1, n, m, n + m - 1 - i)
        mabs = max(mabs, abs(v - expect))
    print("  ones(%d) * ones(%d): max_abs=%.3e rel=%.3e (peak=%d)"
          % (n, m, mabs, mabs / ref_peak, ref_peak))
    assert mabs / ref_peak < 1e-9

    # 随机超长序列：抽 50 个位置用定义直接计算做对拍
    n = 120_000
    a, b = rand_seq(n), rand_seq(n)
    got = convolve(a, b)
    worst = 0.0
    for _ in range(50):
        k = random.randrange(2 * n - 1)
        lo = max(0, k - n + 1)
        hi = min(k, n - 1)
        expect = sum(a[i] * b[k - i] for i in range(lo, hi + 1))
        worst = max(worst, abs(got[k] - expect))
    print("  随机 n=m=%d 抽点50处: max_abs=%.3e" % (n, worst))
    assert worst < 1e-4


def test_error_vs_length():
    print("== 误差随长度变化（随机均匀[-1,1]，等长序列）==")
    print("  %8s %12s %12s %12s" % ("长度", "max_abs", "max_rel", "FFT点数"))
    for n in (16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192):
        a, b = rand_seq(n), rand_seq(n)
        got = convolve(a, b)
        ref = naive_convolve(a, b)
        mabs, mrel = max_err(got, ref)
        print("  %8d %12.3e %12.3e %12d" % (n, mabs, mrel, next_pow2(2 * n - 1)))


def _timeit(fn, repeats):
    best = float("inf")
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def test_timing():
    print("== 耗时对比：朴素 O(n^2) vs FFT O(n log n)（等长序列，取多次最优）==")
    print("  %8s %12s %12s %10s" % ("长度", "朴素(ms)", "FFT(ms)", "加速比"))
    crossover = None
    for n in (8, 16, 24, 32, 48, 64, 96, 128, 192, 256, 512,
              1024, 2048, 4096, 8192):
        a, b = rand_seq(n), rand_seq(n)
        rep = max(1, 20_000_000 // (n * n))
        t_naive = _timeit(lambda: naive_convolve(a, b), min(rep, 200))
        t_fft = _timeit(lambda: convolve(a, b, naive_threshold=0),
                        min(rep, 200))
        ratio = t_naive / t_fft
        if crossover is None and t_fft < t_naive:
            crossover = n
        print("  %8d %12.3f %12.3f %9.2fx" % (n, t_naive * 1e3, t_fft * 1e3, ratio))
    if crossover:
        print("  => 本机从长度约 %d 起 FFT 卷积更划算" % crossover)


if __name__ == "__main__":
    test_fft_roundtrip()
    test_correctness()
    test_edge_cases()
    test_long_sequence()
    test_error_vs_length()
    test_timing()
    print("\n全部测试通过。")
