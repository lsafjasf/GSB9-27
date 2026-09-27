"""Iterative radix-2 FFT / IFFT and FFT-based convolution (stdlib only).

Padding strategy
----------------
``fft`` requires a power-of-two length. ``fft_convolve`` pads both inputs
with zeros to ``n = next_pow2(len(a) + len(b) - 1)`` before transforming.
Zero-padding in the time domain corresponds to *over*-sampling the spectrum,
so linear convolution is exact (no circular wrap-around); the only cost is
at most 2x extra work when ``len(a)+len(b)-1`` is just above a power of two.

Extreme-magnitude handling
--------------------------
Before transforming, each input is divided by its own max-abs scale and the
result is multiplied back by the product of the scales. This keeps all
intermediate values O(1), so inputs like 1e300 (overflow) or 1e-300
(underflow -> spurious all-zero output) are handled correctly.
"""

import cmath
import math

__all__ = ["fft", "ifft", "next_pow2", "fft_convolve", "naive_convolve"]


def next_pow2(n):
    """Smallest power of two >= n (n >= 1)."""
    p = 1
    while p < n:
        p <<= 1
    return p


def _bit_reverse_permute(a):
    n = len(a)
    bits = n.bit_length() - 1
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]


def fft(a, inverse=False):
    """In-place-style iterative radix-2 Cooley-Tukey FFT.

    Returns a new list of complex numbers. Length must be a power of two;
    shorter inputs are zero-padded to the next power of two (documented
    strategy: zero-padding only interpolates the spectrum, it never
    changes the underlying transform values of the original signal).
    """
    n = next_pow2(max(1, len(a)))
    a = [complex(x) for x in a] + [0j] * (n - len(a))
    _bit_reverse_permute(a)
    sign = 1.0 if inverse else -1.0
    size = 2
    while size <= n:
        half = size >> 1
        # twiddle step for this stage
        w_step = cmath.exp(complex(0.0, sign * 2.0 * math.pi / size))
        for start in range(0, n, size):
            w = 1.0 + 0j
            for k in range(start, start + half):
                u = a[k]
                v = a[k + half] * w
                a[k] = u + v
                a[k + half] = u - v
                w *= w_step
        size <<= 1
    if inverse:
        inv_n = 1.0 / n
        a = [x * inv_n for x in a]
    return a


def ifft(a):
    """Inverse FFT. Same zero-padding convention as ``fft``."""
    return fft(a, inverse=True)


def _scale_of(seq):
    m = 0.0
    for x in seq:
        ax = abs(x)
        if ax > m:
            m = ax
    return m


def fft_convolve(a, b):
    """Linear convolution of real/complex sequences via FFT.

    Returns a list of complex numbers of length len(a)+len(b)-1.
    Inputs are pre-normalized so extreme magnitudes neither overflow
    nor underflow to a spurious all-zero result.
    """
    if not a or not b:
        return []
    out_len = len(a) + len(b) - 1
    sa = _scale_of(a)
    sb = _scale_of(b)
    if sa == 0.0 or sb == 0.0:
        return [0j] * out_len
    na = [x / sa for x in a]
    nb = [x / sb for x in b]
    n = next_pow2(out_len)
    fa = fft(na + [0.0] * (n - len(na)))
    fb = fft(nb + [0.0] * (n - len(nb)))
    fc = [x * y for x, y in zip(fa, fb)]
    c = ifft(fc)
    scale = sa * sb
    return [x * scale for x in c[:out_len]]


def naive_convolve(a, b):
    """O(n*m) direct linear convolution (reference implementation)."""
    if not a or not b:
        return []
    out = [0j] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        if x == 0:
            continue
        for j, y in enumerate(b):
            out[i + j] += x * y
    return out
