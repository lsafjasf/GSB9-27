"""Deterministic clustering library (Python 3, standard library only).

Design goals
------------
1. Deterministic: same input -> same grouping on every run and every machine.
   No randomness anywhere; seeds are chosen by farthest-first (maximin)
   traversal with lowest-index tie-breaking.
2. Explicit empty-cluster handling: empty clusters are reseeded to the
   currently worst-served point; every occurrence is counted and reported.
3. Automatic k selection via the Calinski-Harabasz variance-ratio
   criterion, computed deterministically on the full dataset.
"""

__all__ = ["kmeans", "choose_k", "predict", "silhouette_score",
           "calinski_harabasz", "KMeansResult", "KSelection"]


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------

def _sq_dist(a, b):
    s = 0.0
    for x, y in zip(a, b):
        d = x - y
        s += d * d
    return s


def _dist(a, b):
    return _sq_dist(a, b) ** 0.5


def _centroid(points):
    dim = len(points[0])
    acc = [0.0] * dim
    for p in points:
        for j, v in enumerate(p):
            acc[j] += v
    inv = 1.0 / len(points)
    return [v * inv for v in acc]


def _all_identical(points):
    first = points[0]
    for p in points[1:]:
        if p != first:
            return False
    return True


# ---------------------------------------------------------------------------
# Deterministic seeding: farthest-first (maximin) traversal
# ---------------------------------------------------------------------------

def _farthest_first(points, k):
    """Pick k seed indices deterministically.

    Seed 1: point farthest from the global centroid.
    Seed i: point whose distance to the nearest already-chosen seed is maximal.
    All ties are broken by lowest index, so the result is fully deterministic.
    """
    n = len(points)
    cent = _centroid(points)
    first, best = 0, -1.0
    for i, p in enumerate(points):
        d = _sq_dist(p, cent)
        if d > best:
            best, first = d, i
    seeds = [first]
    min_d = [_sq_dist(p, points[first]) for p in points]
    while len(seeds) < k:
        nxt, best = 0, -1.0
        for i in range(n):
            if min_d[i] > best:
                best, nxt = min_d[i], i
        seeds.append(nxt)
        pn = points[nxt]
        for i in range(n):
            d = _sq_dist(points[i], pn)
            if d < min_d[i]:
                min_d[i] = d
    return seeds


# ---------------------------------------------------------------------------
# K-means
# ---------------------------------------------------------------------------

class KMeansResult:
    """Result of a deterministic k-means run."""

    def __init__(self, labels, centers, inertia, iterations, converged,
                 requested_k, empty_cluster_events):
        self.labels = labels                  # cluster id per input point
        self.centers = centers                # final cluster centers
        self.inertia = inertia                # sum of squared distances to centers
        self.iterations = iterations          # Lloyd iterations performed
        self.converged = converged            # stopped before max_iter
        self.requested_k = requested_k        # k as requested by the caller
        self.k = len(centers)                 # effective k (clamped to n)
        self.clamped = self.k < requested_k   # True when n < requested k
        self.empty_cluster_events = empty_cluster_events  # empties seen during fit

    @property
    def empty_clusters(self):
        """Number of clusters that ended up with no assigned points."""
        used = set(self.labels)
        return sum(1 for c in range(self.k) if c not in used)

    def summary(self):
        return {
            "requested_k": self.requested_k,
            "effective_k": self.k,
            "clamped_to_n": self.clamped,
            "iterations": self.iterations,
            "converged": self.converged,
            "inertia": self.inertia,
            "empty_cluster_events": self.empty_cluster_events,
            "empty_clusters_final": self.empty_clusters,
        }


def kmeans(points, k, max_iter=100, init_centers=None):
    """Deterministic Lloyd's k-means.

    points:       list of equal-length numeric sequences.
    k:            requested number of clusters (clamped to len(points)).
    init_centers: optional warm-start centers (e.g. from a previous batch).
                  When omitted, deterministic farthest-first seeding is used.

    Empty-cluster policy: RESEED. A cluster that receives no points is moved
    to the point farthest from its currently assigned center (deterministic,
    lowest index on ties). If no such point exists (all points coincide with
    their centers, e.g. fully identical input), the empty cluster is kept.
    Every empty-cluster occurrence is counted in `empty_cluster_events`.
    """
    n = len(points)
    if n == 0:
        raise ValueError("cannot cluster an empty dataset")
    if k < 1:
        raise ValueError("k must be >= 1")
    k_eff = min(k, n)

    if init_centers is not None:
        centers = [list(c) for c in init_centers[:k_eff]]
        if len(centers) < k_eff:  # top up deterministically
            for idx in _farthest_first(points, k_eff):
                if len(centers) >= k_eff:
                    break
                centers.append(list(points[idx]))
    else:
        centers = [list(points[i]) for i in _farthest_first(points, k_eff)]

    dim = len(points[0])
    labels = [-1] * n
    empty_events = 0
    iterations = 0
    converged = False

    for it in range(1, max_iter + 1):
        iterations = it
        changed = False

        # Assignment step (ties -> lowest cluster index: deterministic).
        for i in range(n):
            p = points[i]
            best_c = 0
            c0 = centers[0]
            best_d = 0.0
            for j in range(dim):
                dd = p[j] - c0[j]
                best_d += dd * dd
            for c in range(1, k_eff):
                cc = centers[c]
                d = 0.0
                for j in range(dim):
                    dd = p[j] - cc[j]
                    d += dd * dd
                if d < best_d:
                    best_d, best_c = d, c
            if labels[i] != best_c:
                labels[i] = best_c
                changed = True

        # Update step.
        sums = [[0.0] * dim for _ in range(k_eff)]
        counts = [0] * k_eff
        for i in range(n):
            c = labels[i]
            counts[c] += 1
            sp = sums[c]
            p = points[i]
            for j in range(dim):
                sp[j] += p[j]

        reseeded = False
        for c in range(k_eff):
            if counts[c] > 0:
                inv = 1.0 / counts[c]
                centers[c] = [v * inv for v in sums[c]]
                continue
            # Empty cluster: count it and try to reseed.
            empty_events += 1
            fi, fd = 0, -1.0
            for i in range(n):
                d = _sq_dist(points[i], centers[labels[i]])
                if d > fd:
                    fd, fi = d, i
            if fd > 0.0:
                centers[c] = list(points[fi])
                reseeded = True
            # fd == 0: degenerate data (all points on their centers);
            # keep the empty cluster as-is.

        if not changed and not reseeded:
            converged = True
            break

    inertia = 0.0
    for i in range(n):
        inertia += _sq_dist(points[i], centers[labels[i]])

    return KMeansResult(labels, centers, inertia, iterations, converged,
                        requested_k=k, empty_cluster_events=empty_events)


def predict(centers, points):
    """Assign new points (e.g. a later batch) to existing centers.

    Deterministic: nearest center, lowest index on ties.
    """
    out = []
    for p in points:
        best_c, best_d = 0, _sq_dist(p, centers[0])
        for c in range(1, len(centers)):
            d = _sq_dist(p, centers[c])
            if d < best_d:
                best_d, best_c = d, c
        out.append(best_c)
    return out


# ---------------------------------------------------------------------------
# Automatic selection of k
# ---------------------------------------------------------------------------

def silhouette_score(points, labels, sample_size=500):
    """Mean silhouette coefficient on a deterministic evenly-spaced sample.

    Uses Euclidean distances (the standard definition). Returns a value in
    [-1, 1]; higher means better separated clusters. Returns 0.0 when fewer
    than two clusters are present in the sample.
    """
    n = len(points)
    if n <= sample_size:
        idx = list(range(n))
    else:
        step = n / sample_size
        idx = [int(i * step) for i in range(sample_size)]

    members = {}
    for i in idx:
        members.setdefault(labels[i], []).append(i)
    if len(members) < 2:
        return 0.0

    total = 0.0
    for i in idx:
        own = members[labels[i]]
        if len(own) > 1:
            a = sum(_dist(points[i], points[j]) for j in own if j != i)
            a /= (len(own) - 1)
        else:
            a = 0.0
        b = None
        for c, js in members.items():
            if c == labels[i]:
                continue
            d = sum(_dist(points[i], points[j]) for j in js) / len(js)
            if b is None or d < b:
                b = d
        denom = a if a > b else b
        total += 0.0 if denom == 0.0 else (b - a) / denom
    return total / len(idx)


def calinski_harabasz(points, labels):
    """Calinski-Harabasz variance-ratio index (higher is better).

    Computed on the full dataset in O(n * k); deterministic. Returns
    float('inf') for a perfect fit (zero within-cluster dispersion) and 0.0
    when fewer than two clusters are present.
    """
    n = len(points)
    members = {}
    for i, lab in enumerate(labels):
        members.setdefault(lab, []).append(i)
    if len(members) < 2:
        return 0.0
    overall = _centroid(points)
    within = 0.0
    between = 0.0
    for idxs in members.values():
        c = _centroid([points[i] for i in idxs])
        for i in idxs:
            within += _sq_dist(points[i], c)
        between += len(idxs) * _sq_dist(c, overall)
    if within == 0.0:
        return float("inf")
    return (between / (len(members) - 1)) / (within / (n - len(members)))


class KSelection:
    """Outcome of automatic k selection."""

    def __init__(self, k, metric, scores, reason):
        self.k = k
        self.metric = metric
        self.scores = scores      # {k: metric value}; empty for degenerate data
        self.reason = reason      # human-readable selection rationale

    def summary(self):
        return {"k": self.k, "metric": self.metric,
                "scores": self.scores, "reason": self.reason}


def choose_k(points, k_min=2, k_max=10, max_iter=100):
    """Pick the number of clusters automatically.

    Metric: Calinski-Harabasz variance-ratio index, computed on the full
        dataset (deterministic, O(n * k) per candidate). It isolates
        outliers correctly, unlike the mean silhouette coefficient, which
        a single far-away outlier can bias towards merging real clusters.
    Rule:   the k with the highest index wins; ties go to the smaller k.
    Degenerate inputs (n < 2 or all points identical) select k = 1 directly,
    because a variance ratio is undefined for a single cluster.
    """
    n = len(points)
    if n == 0:
        raise ValueError("cannot cluster an empty dataset")
    if n < 2:
        return KSelection(1, "calinski_harabasz", {},
                          "fewer than 2 points; a single cluster is the only "
                          "valid grouping")
    if _all_identical(points):
        return KSelection(1, "calinski_harabasz", {},
                          "all points are identical; any split is arbitrary, "
                          "so one cluster is reported")

    lo = max(2, k_min)
    hi = min(k_max, n - 1)
    if hi < lo:
        return KSelection(1, "calinski_harabasz", {},
                          "not enough points to evaluate k >= 2")

    scores = {}
    best_k, best_s = None, None
    for k in range(lo, hi + 1):
        res = kmeans(points, k, max_iter=max_iter)
        s = calinski_harabasz(points, res.labels)
        scores[k] = s
        if best_s is None or s > best_s:  # strict: ties keep the smaller k
            best_k, best_s = k, s

    reason = ("k=%d maximises the Calinski-Harabasz index (%.4f) over "
              "candidates %d..%d; smaller k preferred on ties"
              % (best_k, best_s, lo, hi))
    return KSelection(best_k, "calinski_harabasz", scores, reason)
