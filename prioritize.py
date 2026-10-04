"""Coverage-based test prioritization (greedy max-gain), stdlib only.

Given a mapping ``test_id -> set of covered lines``, produce an execution
order that reaches full coverage as early as possible: repeatedly pick the
test covering the most not-yet-covered lines (maximum marginal gain).

Determinism / execution-order independence
------------------------------------------
The result depends only on the *sets* of covered lines, never on the
iteration/insertion order of the input mapping. Ties in marginal gain are
broken by a total, deterministic rule:

1. larger total coverage size first (the test is "stronger" overall);
2. then lexicographically smaller test id.

Since test ids are unique, the rule is a total order on candidates, so the
greedy choice is unique at every step and the final order is a pure
function of the coverage sets.

Tests covering nothing (or nothing new) sink to the tail, ordered by the
same deterministic rule.
"""

from __future__ import annotations

import json
import sys


def _rank_key(uncovered, total_sizes):
    """Deterministic candidate key: max gain, then max total size, then min id."""

    def key(tid):
        return (-len(uncovered[tid]), -total_sizes[tid], tid)

    return key


def prioritize(coverage):
    """Return test ids ordered by greedy marginal coverage gain.

    Args:
        coverage: mapping of test id -> iterable of covered line ids.

    Returns:
        list of test ids. Pure function of the coverage *sets*: any input
        mapping with equal sets yields the identical order.
    """
    uncovered = {tid: frozenset(lines) for tid, lines in coverage.items()}
    total_sizes = {tid: len(lines) for tid, lines in uncovered.items()}
    covered = set()
    order = []
    while uncovered:
        tid = min(uncovered, key=_rank_key(uncovered, total_sizes))
        order.append(tid)
        covered |= uncovered.pop(tid)
        # Shrink remaining sets so later gain computations stay cheap.
        uncovered = {t: lines - covered for t, lines in uncovered.items()}
    return order


def cumulative_curve(coverage, order=None, top=None):
    """Cumulative coverage curve along an order.

    Args:
        coverage: mapping of test id -> iterable of covered line ids.
        order: execution order; defaults to ``prioritize(coverage)``.
        top: keep only the first ``top`` rows (None = all).

    Returns:
        list of dicts with keys: rank, test, gain, covered, pct
        (pct = covered / total_coverable_lines * 100, rounded to 2dp;
        100.0 when the union of all coverage is empty).
    """
    sets = {tid: frozenset(lines) for tid, lines in coverage.items()}
    if order is None:
        order = prioritize(coverage)
    total = len(frozenset().union(*sets.values())) if sets else 0
    covered = set()
    rows = []
    for rank, tid in enumerate(order, 1):
        new = sets[tid] - covered
        covered |= sets[tid]
        pct = round(len(covered) / total * 100, 2) if total else 100.0
        rows.append(
            {
                "rank": rank,
                "test": tid,
                "gain": len(new),
                "covered": len(covered),
                "pct": pct,
            }
        )
        if top is not None and rank >= top:
            break
    return rows


def _demo():
    # Sample regression suite: 8 tests over lines of a fictional module.
    coverage = {
        "test_login": {1, 2, 3, 4, 5},
        "test_logout": {4, 5, 6},
        "test_upload": {2, 3, 7, 8, 9, 10},
        "test_download": {7, 8},
        "test_profile": {11, 12, 13},
        "test_search": {10, 13, 14},
        "test_empty": set(),
        "test_noop": set(),
    }
    order = prioritize(coverage)
    curve = cumulative_curve(coverage, order)
    report = {
        "order": order,
        "total_coverable_lines": curve[-1]["covered"] if curve else 0,
        "cumulative_curve": curve,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _demo()
