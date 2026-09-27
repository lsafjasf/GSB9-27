"""Agglomerative hierarchical clustering (standard library only).

Algorithm: nearest-neighbor chain (NN-chain) with Lance-Williams in-place
updates on a condensed distance matrix.

  - Time:  O(n^2)          (each merge costs O(current #clusters))
  - Space: O(n^2) condensed matrix  (n*(n-1)/2 float64, array('d'))

Supported linkage (inter-cluster distance) definitions:
  - "single"  : d(A,B) = min  { d(a,b) : a in A, b in B }
  - "average" : d(A,B) = mean { d(a,b) : a in A, b in B }   (UPGMA)

Both are reducible (merging cannot create a distance smaller than the one
just merged), which is exactly the precondition of the NN-chain algorithm,
so the chain never needs to backtrack incorrectly and the result is
identical to the naive "recompute everything" algorithm.

Merge history is returned in the same layout as scipy.cluster.hierarchy.linkage:
each row is (id_a, id_b, distance, size_of_new_cluster).  Original points are
ids 0..n-1; the cluster created by the i-th merge gets id n+i.  Distances are
monotonically non-decreasing, so the history can be fed directly to a
dendrogram plotter or scanned for a "knee" to choose a cut.
"""

from array import array
from math import sqrt

__all__ = ["hierarchical_clustering", "cut_tree", "Merge"]


class Merge(tuple):
    """One merge step: (id_a, id_b, distance, new_cluster_size)."""

    __slots__ = ()

    def __new__(cls, id_a, id_b, distance, size):
        return tuple.__new__(cls, (id_a, id_b, distance, size))

    @property
    def id_a(self):
        return self[0]

    @property
    def id_b(self):
        return self[1]

    @property
    def distance(self):
        return self[2]

    @property
    def size(self):
        return self[3]


def _default_distance(p, q):
    return sqrt(sum((a - b) * (a - b) for a, b in zip(p, q)))


def _condensed_index(n, i, j):
    # index of pair (i, j), i < j, in a condensed n x n distance matrix
    return n * i - (i * (i + 1)) // 2 + (j - i - 1)


def hierarchical_clustering(points, linkage="single", dist=None):
    """Cluster `points` (a sequence of points) agglomeratively.

    Parameters
    ----------
    points : sequence
        Anything accepted by `dist`; with the default metric, sequences of
        numbers of equal length.
    linkage : "single" | "average"
        Inter-cluster distance definition.
    dist : callable, optional
        dist(p, q) -> float.  Defaults to Euclidean distance.

    Returns
    -------
    list[Merge]
        n-1 merges in order of increasing distance.  See module docstring
        for the id convention.
    """
    # The condensed matrix stores, per cluster pair:
    #   single  -> the minimum point-pair distance (an exact stored value)
    #   average -> the SUM of point-pair distances; the linkage value is
    #              sum / (size_a * size_b), computed on demand
    # Keeping sums (not rounded means) makes the merge update a single
    # exact-float-operation (addition), so results are reproducible and
    # tie-breaking on equal distances stays deterministic.
    if linkage == "single":
        def value(a, b, sizes):
            return matrix[_condensed_index(n, a, b) if a < b
                          else _condensed_index(n, b, a)]

        def combine(v_ik, v_jk):
            return v_ik if v_ik < v_jk else v_jk
    elif linkage == "average":
        def value(a, b, sizes):
            s = matrix[_condensed_index(n, a, b) if a < b
                       else _condensed_index(n, b, a)]
            return s / (sizes[a] * sizes[b])

        def combine(v_ik, v_jk):
            return v_ik + v_jk
    else:
        raise ValueError("linkage must be 'single' or 'average', got %r" % (linkage,))

    if dist is None:
        dist = _default_distance

    pts = list(points)
    n = len(pts)
    if n < 2:
        return []

    # --- condensed distance matrix (upper triangle, row-major) -------------
    matrix = array("d", bytes(8 * (n * (n - 1) // 2)))
    for i in range(n):
        base = _condensed_index(n, i, i + 1)
        pi = pts[i]
        for off, j in enumerate(range(i + 1, n)):
            matrix[base + off] = dist(pi, pts[j])

    def get(i, j):
        if i < j:
            return matrix[_condensed_index(n, i, j)]
        return matrix[_condensed_index(n, j, i)]

    def put(i, j, value):
        if i < j:
            matrix[_condensed_index(n, i, j)] = value
        else:
            matrix[_condensed_index(n, j, i)] = value

    # --- nearest-neighbor chain --------------------------------------------
    active = [True] * n
    sizes = [1] * n
    active_count = n
    chain = []
    merges = []

    while active_count > 1:
        if not chain:
            # start a new chain at any active cluster
            for i in range(n):
                if active[i]:
                    chain.append(i)
                    break
        a = chain[-1]
        # nearest active neighbor of `a`; ties broken by smaller id so the
        # result is deterministic (and matches the naive reference, which
        # scans pairs in the same order)
        best = -1
        best_d = float("inf")
        for b in range(n):
            if b == a or not active[b]:
                continue
            d = value(a, b, sizes)
            if d < best_d:
                best_d = d
                best = b
        if len(chain) >= 2 and best == chain[-2]:
            # reciprocal nearest neighbors -> merge (a, best)
            b = best
            if b < a:
                a, b = b, a
            na, nb = sizes[a], sizes[b]
            merges.append(Merge(a, b, best_d, na + nb))
            # Lance-Williams update: fold b's row/column into a's
            for k in range(n):
                if k == a or k == b or not active[k]:
                    continue
                put(a, k, combine(get(a, k), get(b, k)))
            active[b] = False
            sizes[a] = na + nb
            active_count -= 1
            chain.pop()  # drop a
            chain.pop()  # drop b
        else:
            chain.append(best)

    # The loop above records merges between *root* ids in 0..n-1 (cluster b
    # is absorbed into cluster a in place) and in chain order, which is not
    # globally sorted by distance.  Re-emit the history in the standard
    # form (same convention as scipy.cluster.hierarchy.linkage):
    #
    #   * merges sorted by distance -- a stable sort keeps a valid replay
    #     order, because reducible linkages give parent height >= child
    #     height and equal-height merges keep their creation order;
    #   * the cluster created by the i-th emitted merge gets id n+i, and
    #     later merges refer to it by that id.
    order = sorted(range(len(merges)), key=lambda i: merges[i][2])
    current = list(range(n))  # root id -> id of the cluster it represents
    out = []
    for pos, old in enumerate(order):
        a, b, d, s = merges[old]
        ca, cb = current[a], current[b]
        if cb < ca:
            ca, cb = cb, ca
        out.append(Merge(ca, cb, d, s))
        current[a] = n + pos  # a is the surviving root
        current[b] = -1       # b was absorbed
    return out


def cut_tree(merges, n_points, n_clusters=None, threshold=None):
    """Cut a merge history into flat clusters.

    Exactly one of `n_clusters` / `threshold` must be given.  Cutting at k
    clusters means replaying the first n-k merges; cutting at a distance
    threshold replays every merge with distance <= threshold.

    Returns a list of n_points integer labels (0-based, ordered by the
    smallest member id of each cluster).
    """
    if (n_clusters is None) == (threshold is None):
        raise ValueError("give exactly one of n_clusters / threshold")
    n = n_points
    if n_clusters is not None:
        if not 1 <= n_clusters <= n:
            raise ValueError("n_clusters must be in [1, n_points]")
        steps = n - n_clusters
    else:
        steps = sum(1 for m in merges if m.distance <= threshold)

    parent = list(range(2 * n - 1))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for step, m in enumerate(merges):
        if step >= steps:
            break
        new_id = n + step
        parent[find(m.id_a)] = new_id
        parent[find(m.id_b)] = new_id

    labels = {}
    out = []
    for i in range(n):
        root = find(i)
        if root not in labels:
            labels[root] = len(labels)
        out.append(labels[root])
    return out
