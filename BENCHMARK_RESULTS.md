# Degenerate-input timing comparison

- Python: 3.12.3 (CPython)
- Platform: Linux-6.18.33.1-microsoft-standard-WSL2-x86_64-with-glibc2.39
- Times are best-of repeats, seconds.
- Doubling input size: linear work grows ~2x, quadratic work ~4x.

## Worst case A: all-equal (pattern length = n/2)

Small scale — all three algorithms (naive runs once):

| n | naive (s) | KMP (s) | Z (s) | naive x2 ratio | KMP x2 ratio | Z x2 ratio |
|---|-----------|---------|-------|----------------|--------------|------------|
| 5000 | 0.2050 | 0.00046 | 0.00139 | 1.00x | 1.00x | 1.00x |
| 10000 | 0.8263 | 0.00091 | 0.00271 | 4.03x | 1.99x | 1.95x |
| 20000 | 3.3364 | 0.00183 | 0.00554 | 4.04x | 2.01x | 2.05x |

Large scale — linear algorithms only (naive would take minutes):

| n | KMP (s) | Z (s) | KMP x2 ratio | Z x2 ratio |
|---|---------|-------|--------------|------------|
| 200000 | 0.0202 | 0.0579 | 1.00x | 1.00x |
| 400000 | 0.0406 | 0.1170 | 2.01x | 2.02x |
| 800000 | 0.0807 | 0.2376 | 1.99x | 2.03x |

## Worst case B: near-miss periodic (mismatch at last char)

Small scale — all three algorithms (naive runs once):

| n | naive (s) | KMP (s) | Z (s) | naive x2 ratio | KMP x2 ratio | Z x2 ratio |
|---|-----------|---------|-------|----------------|--------------|------------|
| 5000 | 0.0993 | 0.00054 | 0.00123 | 1.00x | 1.00x | 1.00x |
| 10000 | 0.4051 | 0.00109 | 0.00248 | 4.08x | 2.04x | 2.01x |
| 20000 | 1.6935 | 0.00224 | 0.00500 | 4.18x | 2.05x | 2.02x |

Reading: naive ratios stay near 4x (quadratic), while KMP/Z stay
near 2x (linear) and handle inputs 40x larger in much less wall time.

