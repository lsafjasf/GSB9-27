"""Rank-correlation statistics (stdlib only), used for the annotation check."""
from __future__ import annotations

import math


def _ranks(values):
    """Average ranks for ties. Highest value gets rank 1."""
    order = sorted(range(len(values)), key=lambda i: -values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs, ys):
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return 1.0  # constant series: treat as perfect agreement of ranks
    return cov / math.sqrt(vx * vy)


def spearman(xs, ys):
    """Spearman rank correlation between two equal-length sequences."""
    assert len(xs) == len(ys) and len(xs) >= 2
    return _pearson(_ranks(list(xs)), _ranks(list(ys)))


def kendall_tau_b(xs, ys):
    """Kendall tau-b (tie-corrected) rank correlation."""
    assert len(xs) == len(ys) and len(xs) >= 2
    n = len(xs)
    concordant = discordant = 0
    ties_x = ties_y = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx = (xs[i] > xs[j]) - (xs[i] < xs[j])
            dy = (ys[i] > ys[j]) - (ys[i] < ys[j])
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                ties_x += 1
            elif dy == 0:
                ties_y += 1
            elif dx == dy:
                concordant += 1
            else:
                discordant += 1
    denom = math.sqrt((concordant + discordant + ties_x) *
                      (concordant + discordant + ties_y))
    if denom == 0:
        return 1.0
    return (concordant - discordant) / denom
