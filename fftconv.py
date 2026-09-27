"""fftconv — 纯标准库实现的迭代式 FFT 与快速卷积。

设计要点
--------
1. 迭代式基-2 Cooley-Tukey FFT（位逆序重排 + 自底向上蝶形），
   避免递归开销，正/逆变换共用一个入口。
2. 任意长度卷积：线性卷积长度为 n+m-1，统一补零到 >= n+m-1 的
   最小二次幂再做变换。补零不改变线性卷积结果——多出来的只是
   尾部的零，截取前 n+m-1 项即为精确（浮点意义下）的线性卷积。
   注意：若直接对原长做同长 FFT 相乘，得到的是"循环卷积"，
   会发生混叠；补零到 n+m-1 正是消除混叠的标准做法。
3. 幅度归一化：卷积前把每条序列除以其最大幅值（O(1) 量级），
   卷积后再乘回比例因子。这样当输入幅值极小（如 1e-170）时，
   频域乘积不会因 float64 下溢而整段变零；幅值极大时也能
   尽量避免中间结果上溢。只要真实结果在 float64 可表示范围内，
   就能得到非零答案。
4. 短序列直接走朴素 O(n*m) 卷积（NAIVE_THRESHOLD 以下），
   避免 FFT 的固定开销。
"""

import cmath
import math

__all__ = ["fft", "ifft", "next_pow2", "convolve", "naive_convolve"]

NAIVE_THRESHOLD = 48  # 输出长度 <= 该值时直接用朴素卷积


def next_pow2(n):
    """返回 >= n 的最小 2 的幂（n >= 1）。"""
    if n < 1:
        raise ValueError("n must be >= 1")
    return 1 << (n - 1).bit_length()


def fft(x, inverse=False):
    """迭代式基-2 FFT。len(x) 必须是 2 的幂，否则抛 ValueError。"""
    n = len(x)
    if n == 0:
        return []
    if n & (n - 1):
        raise ValueError("fft 要求长度为 2 的幂，得到 %d" % n)

    a = [complex(v) for v in x]

    # 位逆序重排（in-place）
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]

    # 自底向上蝶形合并
    sign = 1.0 if inverse else -1.0
    length = 2
    while length <= n:
        half = length >> 1
        wlen = cmath.exp(complex(0.0, sign * math.pi / half))
        for i in range(0, n, length):
            w = 1.0 + 0.0j
            for k in range(i, i + half):
                u = a[k]
                v = a[k + half] * w
                a[k] = u + v
                a[k + half] = u - v
                w *= wlen
        length <<= 1

    if inverse:
        inv_n = 1.0 / n
        a = [v * inv_n for v in a]
    return a


def ifft(x):
    """逆变换，等价于 fft(x, inverse=True)。"""
    return fft(x, inverse=True)


def naive_convolve(a, b):
    """朴素 O(n*m) 线性卷积，作为对拍基准。"""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return []
    out = [0.0] * (n + m - 1)
    for i, ai in enumerate(a):
        if ai == 0:
            continue
        for j in range(m):
            out[i + j] += ai * b[j]
    return out


def _max_abs(seq):
    m = 0.0
    for v in seq:
        av = abs(v)
        if av > m:
            m = av
    return m


def convolve(a, b, naive_threshold=NAIVE_THRESHOLD):
    """基于 FFT 的线性卷积，支持任意长度、实数或复数输入。

    返回长度为 n+m-1 的列表；输入全为实数时返回实数列表。
    """
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return []
    out_len = n + m - 1
    if out_len <= naive_threshold:
        return naive_convolve(a, b)

    # 幅度归一化：把每条序列缩放到 O(1)，防止频域乘积下溢/上溢。
    # 全零序列比例因子取 1（此时结果必然全零，直接走流程即可）。
    sa = _max_abs(a) or 1.0
    sb = _max_abs(b) or 1.0

    size = next_pow2(out_len)
    fa = fft([v / sa for v in a] + [0.0] * (size - n))
    fb = fft([v / sb for v in b] + [0.0] * (size - m))
    fc = [x * y for x, y in zip(fa, fb)]
    c = fft(fc, inverse=True)

    is_real = all(isinstance(v, (int, float)) for v in a) and \
              all(isinstance(v, (int, float)) for v in b)
    out = []
    for i in range(out_len):
        # 分两步乘回比例因子，避免 sa*sb 本身下溢/上溢。
        v = c[i] * sa * sb
        out.append(v.real if is_real else v)
    return out
