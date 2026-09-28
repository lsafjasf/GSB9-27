"""Drift detection library (Python 3 standard library only).

Metric families:

1. Binned distribution difference (binning frozen at fit time)
   - PSI (Population Stability Index): magnitude metric, reported as-is.
   - G-test (likelihood-ratio chi-square) on the same bins: a calibrated
     p-value that adapts to the current batch size (raw PSI thresholds are
     not sample-size aware and over-alert on small batches).
   - Tail test: G-test on a 3-bin partition defined by the baseline 1% and
     99% quantiles; sensitive to tail-only changes that PSI/KS dilute.

2. Cumulative distribution difference
   - Two-sample Kolmogorov-Smirnov statistic with asymptotic p-value.

Categorical features use PSI + chi-square (exact p via incomplete gamma)
on the frozen baseline vocabulary; unseen categories fold into __OTHER__.

Alert rule (numeric): any calibrated p-value < alpha  ->  alert.
PSI >= psi_warn only raises a "warn" level (magnitude hint, not calibrated).

Usage:
    det = NumericDriftDetector()
    det.fit(baseline_values)
    report = det.score(current_values)
    if report["alert"]:
        ...
"""

import math

__all__ = [
    "NumericDriftDetector",
    "CategoricalDriftDetector",
    "DriftReport",
    "InsufficientDataError",
    "EmptyBaselineError",
    "psi",
    "ks_statistic",
    "ks_pvalue",
    "g_test_pvalue",
    "homogeneity_g_pvalue",
    "chi2_sf",
]

OTHER_CATEGORY = "__OTHER__"


class EmptyBaselineError(ValueError):
    """Raised when fitting on an empty baseline."""


class InsufficientDataError(ValueError):
    """Raised when a batch is too small for a meaningful test."""


# ---------------------------------------------------------------------------
# Special functions (stdlib-only)
# ---------------------------------------------------------------------------

def _gammaincc(a, x):
    """Regularized upper incomplete gamma Q(a, x) = Gamma(a,x)/Gamma(a)."""
    if x <= 0:
        return 1.0
    if x < a + 1.0:
        # series for P, then Q = 1 - P
        ap = a
        term = 1.0 / a
        total = term
        for _ in range(1000):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-14:
                break
        p = total * math.exp(-x + a * math.log(x) - math.lgamma(a))
        return max(0.0, min(1.0, 1.0 - p))
    # continued fraction for Q
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    q = h * math.exp(-x + a * math.log(x) - math.lgamma(a))
    return max(0.0, min(1.0, q))


def chi2_sf(x, df):
    """Survival function P(X >= x) for chi-square with df degrees of freedom."""
    if df <= 0 or x <= 0:
        return 1.0
    return _gammaincc(df / 2.0, x / 2.0)


# ---------------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------------

def psi(expected_props, actual_props, eps=1e-6):
    """Population Stability Index between two discrete distributions."""
    total = 0.0
    for e, a in zip(expected_props, actual_props):
        e = max(e, eps)
        a = max(a, eps)
        total += (a - e) * math.log(a / e)
    return total


def g_test_pvalue(expected_props, actual_counts):
    """Likelihood-ratio G-test of actual_counts against expected proportions.

    Returns (G, p_value). Bins with zero expected proportion are dropped
    (and their observed counts ignored) to keep df well-defined.
    """
    n = sum(actual_counts)
    if n == 0:
        return 0.0, 1.0
    g = 0.0
    df = 0
    for e_prop, o in zip(expected_props, actual_counts):
        if e_prop <= 0:
            continue
        df += 1
        if o > 0:
            g += 2.0 * o * math.log(o / (e_prop * n))
    df -= 1
    if df < 1:
        return g, 1.0
    return g, chi2_sf(g, df)


def homogeneity_g_pvalue(baseline_counts, current_counts):
    """Two-sample likelihood-ratio G-test of homogeneity on binned counts.

    Unlike a one-sample test against baseline proportions, this conditions
    on the pooled bin margins, so baseline sampling noise is accounted for
    and the false-positive rate stays calibrated. Bins with zero pooled
    count are dropped. Returns (G, p_value).
    """
    nb = sum(baseline_counts)
    nc = sum(current_counts)
    if nb == 0 or nc == 0:
        return 0.0, 1.0
    total = nb + nc
    g = 0.0
    df = 0
    for b, c in zip(baseline_counts, current_counts):
        pooled = b + c
        if pooled <= 0:
            continue
        df += 1
        e_b = pooled * nb / total
        e_c = pooled * nc / total
        if b > 0:
            g += 2.0 * b * math.log(b / e_b)
        if c > 0:
            g += 2.0 * c * math.log(c / e_c)
    df -= 1
    if df < 1:
        return g, 1.0
    return g, chi2_sf(g, df)


def ks_statistic(sample_a, sample_b):
    """Two-sample Kolmogorov-Smirnov D statistic (max CDF gap)."""
    a = sorted(sample_a)
    b = sorted(sample_b)
    n, m = len(a), len(b)
    i = j = 0
    cdf_a = cdf_b = 0.0
    d = 0.0
    while i < n and j < m:
        if a[i] == b[j]:
            cdf_a = (i + 1) / n
            cdf_b = (j + 1) / m
            i += 1
            j += 1
        elif a[i] < b[j]:
            cdf_a = (i + 1) / n
            i += 1
        else:
            cdf_b = (j + 1) / m
            j += 1
        gap = abs(cdf_a - cdf_b)
        if gap > d:
            d = gap
    return d


def ks_pvalue(d, n, m):
    """Asymptotic two-sample KS p-value via the Kolmogorov distribution."""
    if n == 0 or m == 0:
        return 1.0
    en = math.sqrt(n * m / (n + m))
    lam = (en + 0.12 + 0.11 / en) * d
    if lam <= 0:
        return 1.0
    s = 0.0
    for k in range(1, 101):
        term = (-1) ** (k - 1) * math.exp(-2.0 * (k * lam) ** 2)
        s += term
        if abs(term) < 1e-10:
            break
    return max(0.0, min(1.0, 2.0 * s))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

class DriftReport(dict):
    """Dict subclass so reports are JSON-serialisable for free."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.__dict__ = self


# ---------------------------------------------------------------------------
# Numeric detector
# ---------------------------------------------------------------------------

def _quantile_edges(sorted_baseline, n_bins):
    """Quantile bin edges from sorted baseline. Interior edges only; the
    outer edges are -inf / +inf so any future value falls into a bin.
    Duplicate edges and edges outside the observed range are removed
    (a constant baseline collapses to a single bin)."""
    n = len(sorted_baseline)
    edges = []
    for k in range(1, n_bins):
        pos = k * n / n_bins
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        edge = sorted_baseline[lo] * (1 - frac) + sorted_baseline[hi] * frac
        if not edges or edge > edges[-1]:
            edges.append(edge)
    lo, hi = sorted_baseline[0], sorted_baseline[-1]
    return [e for e in edges if lo < e < hi]


def _interp_quantile(sorted_vals, q):
    n = len(sorted_vals)
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


class NumericDriftDetector:
    """Numeric drift detector: frozen quantile bins (PSI + G-test),
    KS test, and a tail-mass test on baseline 1%/99% quantiles."""

    def __init__(self, n_bins=10, alpha=0.01, tail_q=0.01,
                 psi_warn=0.1, min_baseline=20, min_batch=20):
        self.n_bins = n_bins
        self.alpha = alpha
        self.tail_q = tail_q
        self.psi_warn = psi_warn
        self.min_baseline = min_baseline
        self.min_batch = min_batch
        self._edges = None
        self._baseline_props = None
        self._baseline_counts = None
        self._baseline_tail_counts = None
        self._baseline_sorted = None
        self._tail_lo = None
        self._tail_hi = None

    # -- fitting ---------------------------------------------------------
    def fit(self, baseline):
        baseline = [float(x) for x in baseline]
        if len(baseline) == 0:
            raise EmptyBaselineError("baseline is empty; cannot derive bins")
        if len(baseline) < self.min_baseline:
            raise InsufficientDataError(
                "baseline has %d samples; need >= %d for stable bins"
                % (len(baseline), self.min_baseline))
        baseline.sort()
        self._baseline_sorted = baseline
        self._edges = _quantile_edges(baseline, self.n_bins)
        self._baseline_counts = self._bin_counts(baseline)
        total = float(len(baseline))
        self._baseline_props = [c / total for c in self._baseline_counts]
        self._tail_lo = _interp_quantile(baseline, self.tail_q)
        self._tail_hi = _interp_quantile(baseline, 1.0 - self.tail_q)
        lo_c = sum(1 for v in baseline if v <= self._tail_lo)
        hi_c = sum(1 for v in baseline if v > self._tail_hi)
        self._baseline_tail_counts = [lo_c, len(baseline) - lo_c - hi_c, hi_c]
        return self

    @property
    def bin_edges(self):
        return list(self._edges) if self._edges is not None else None

    def _bin_counts(self, values):
        counts = [0] * (len(self._edges) + 1)
        for v in values:
            idx = 0
            while idx < len(self._edges) and v > self._edges[idx]:
                idx += 1
            counts[idx] += 1
        return counts

    # -- scoring ---------------------------------------------------------
    def score(self, current):
        if self._edges is None:
            raise RuntimeError("detector is not fitted; call fit() first")
        current = [float(x) for x in current]
        report = DriftReport(
            kind="numeric",
            n_baseline=len(self._baseline_sorted),
            n_current=len(current),
            psi=None,
            g_pvalue=None,
            ks_stat=None,
            ks_pvalue=None,
            tail_pvalue=None,
            level="ok",
            alert=False,
            reasons=[],
        )
        if len(current) == 0:
            report["level"] = "no_data"
            report["reasons"].append("empty current batch")
            return report
        if len(current) < self.min_batch:
            report["level"] = "insufficient_data"
            report["reasons"].append(
                "current batch has %d samples; need >= %d"
                % (len(current), self.min_batch))
            return report

        counts = self._bin_counts(current)
        total = float(len(current))
        props = [c / total for c in counts]
        report["psi"] = psi(self._baseline_props, props)
        _, report["g_pvalue"] = homogeneity_g_pvalue(
            self._baseline_counts, counts)

        d = ks_statistic(self._baseline_sorted, current)
        report["ks_stat"] = d
        report["ks_pvalue"] = ks_pvalue(
            d, len(self._baseline_sorted), len(current))

        # tail-mass test: 3-bin partition at baseline tail quantiles
        if self._tail_lo < self._tail_hi:
            lo_c = sum(1 for v in current if v <= self._tail_lo)
            hi_c = sum(1 for v in current if v > self._tail_hi)
            tail_counts = [lo_c, len(current) - lo_c - hi_c, hi_c]
            _, report["tail_pvalue"] = homogeneity_g_pvalue(
                self._baseline_tail_counts, tail_counts)

        hits = []
        for key, label in (("g_pvalue", "binned_g"), ("ks_pvalue", "ks"),
                           ("tail_pvalue", "tail")):
            p = report[key]
            if p is not None and p < self.alpha:
                hits.append("%s p=%.2g" % (label, p))
        if hits:
            report["level"] = "alert"
            report["alert"] = True
            report["reasons"].extend(hits)
        elif report["psi"] >= self.psi_warn:
            report["level"] = "warn"
            report["reasons"].append("psi=%.4f (magnitude only)" % report["psi"])
        return report


# ---------------------------------------------------------------------------
# Categorical detector
# ---------------------------------------------------------------------------

class CategoricalDriftDetector:
    """Categorical drift: PSI + chi-square on frozen baseline vocabulary.

    Categories unseen at fit time are folded into an '__OTHER__' bucket so
    the support never changes at detection time."""

    def __init__(self, alpha=0.01, psi_warn=0.1,
                 min_baseline=20, min_batch=20, max_categories=1000):
        self.alpha = alpha
        self.psi_warn = psi_warn
        self.min_baseline = min_baseline
        self.min_batch = min_batch
        self.max_categories = max_categories
        self._vocab = None
        self._baseline_props = None
        self._baseline_total = 0

    def fit(self, baseline):
        baseline = list(baseline)
        if len(baseline) == 0:
            raise EmptyBaselineError("baseline is empty; cannot derive vocabulary")
        if len(baseline) < self.min_baseline:
            raise InsufficientDataError(
                "baseline has %d samples; need >= %d"
                % (len(baseline), self.min_baseline))
        counts = {}
        for v in baseline:
            counts[v] = counts.get(v, 0) + 1
        if len(counts) > self.max_categories:
            raise ValueError("too many distinct categories: %d" % len(counts))
        self._vocab = sorted(counts, key=str) + [OTHER_CATEGORY]
        total = float(len(baseline))
        # __OTHER__ gets a small pseudo-count so a rare new category is
        # measured against a non-zero expectation
        self._baseline_counts = [float(counts.get(k, 0)) for k in self._vocab]
        self._baseline_counts[-1] = 0.5
        self._baseline_props = [c / total for c in self._baseline_counts]
        self._baseline_total = len(baseline)
        return self

    def score(self, current):
        if self._vocab is None:
            raise RuntimeError("detector is not fitted; call fit() first")
        current = list(current)
        report = DriftReport(
            kind="categorical",
            n_baseline=self._baseline_total,
            n_current=len(current),
            psi=None,
            chi2=None,
            chi2_pvalue=None,
            level="ok",
            alert=False,
            reasons=[],
        )
        if len(current) == 0:
            report["level"] = "no_data"
            report["reasons"].append("empty current batch")
            return report
        if len(current) < self.min_batch:
            report["level"] = "insufficient_data"
            report["reasons"].append(
                "current batch has %d samples; need >= %d"
                % (len(current), self.min_batch))
            return report

        index = {k: i for i, k in enumerate(self._vocab)}
        counts = [0] * len(self._vocab)
        for v in current:
            counts[index.get(v, index[OTHER_CATEGORY])] += 1

        total = float(len(current))
        cur_props = [c / total for c in counts]
        report["psi"] = psi(self._baseline_props, cur_props)
        _, report["chi2_pvalue"] = homogeneity_g_pvalue(
            self._baseline_counts, [float(c) for c in counts])
        report["chi2"] = _pearson_chi2(self._baseline_props, counts)

        if report["chi2_pvalue"] < self.alpha:
            report["level"] = "alert"
            report["alert"] = True
            report["reasons"].append("chi2 p=%.2g" % report["chi2_pvalue"])
        elif report["psi"] >= self.psi_warn:
            report["level"] = "warn"
            report["reasons"].append("psi=%.4f (magnitude only)" % report["psi"])
        return report


def _pearson_chi2(expected_props, actual_counts):
    n = sum(actual_counts)
    stat = 0.0
    for e_prop, o in zip(expected_props, actual_counts):
        e = e_prop * n
        if e > 0:
            stat += (o - e) ** 2 / e
    return stat
