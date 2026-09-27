"""Naive reference implementations used to cross-check hclust.py.

Both variants recompute inter-cluster distances directly from the raw
points every time they need them -- no distance matrix, no Lance-Williams
updates -- so a total run costs O(n^3) distance evaluations.  They exist
only to validate the fast implementation on small inputs.

  naive_global : textbook version.  Every step recomputes the full
                 cluster-distance matrix and merges the global argmin
                 (ties broken by cluster id).  Used on random data where
                 ties do not occur, so its merge order is well defined and
                 must equal the fast NN-chain result.

  naive_chain  : mirrors the NN-chain control flow of hclust.py exactly
                 (same tie-breaking), but answers every nearest-neighbor
                 query by recomputing all point-pair distances from
                 scratch.  Used on adversarial inputs with heavy ties,
                 where the merge order is tie-break-dependent.
"""

from math import sqrt


def _default_distance(p, q):
    return sqrt(sum((a - b) * (a - b) for a, b in zip(p, q)))


def _cluster_distance(members_a, members_b, points, linkage, dist):
    """Recompute d(A, B) from scratch by scanning all point pairs."""
    if linkage == "single":
        best = float("inf")
        for a in members_a:
            for b in members_b:
                d = dist(points[a], points[b])
                if d < best:
                    best = d
        return best
    if linkage == "average":
        total = 0.0
        count = 0
        for a in members_a:
            for b in members_b:
                total += dist(points[a], points[b])
                count += 1
        return total / count
    raise ValueError("linkage must be 'single' or 'average'")


def naive_global(points, linkage="single", dist=None):
    """Each step: recompute all cluster-pair distances, merge the argmin."""
    if dist is None:
        dist = _default_distance
    pts = [list(p) for p in points]
    clusters = [[i] for i in range(len(pts))]
    merges = []
    while len(clusters) > 1:
        best = None  # (distance, i, j)
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = _cluster_distance(clusters[i], clusters[j], pts, linkage, dist)
                if best is None or (d, i, j) < best:
                    best = (d, i, j)
        d, i, j = best
        merges.append((i, j, d, len(clusters[i]) + len(clusters[j])))
        clusters[i] = clusters[i] + clusters[j]
        del clusters[j]
    return merges


def naive_chain(points, linkage="single", dist=None):
    """NN-chain skeleton identical to hclust.py, distances recomputed."""
    if dist is None:
        dist = _default_distance
    pts = [list(p) for p in points]
    n = len(pts)
    if n < 2:
        return []
    members = [[i] for i in range(n)]
    # immutable merge-tree nodes per root cluster:
    #   ("leaf", point_id)  or  ("node", left, right, formation_step)
    trees = [("leaf", i) for i in range(n)]
    sizes = [1] * n
    active = [True] * n
    active_count = n
    chain = []
    merges = []

    def pair_sum(na, nb):
        # Sum of all point-pair distances between the clusters rooted at
        # na and nb, recomputed from the leaves.  The recursion expands
        # the later-formed cluster first, so the float association order
        # is exactly the one the fast implementation's cached sums have.
        if na[0] == "leaf" and nb[0] == "leaf":
            return dist(pts[na[1]], pts[nb[1]])
        if na[0] == "node" and (nb[0] == "leaf" or na[3] > nb[3]):
            return pair_sum(na[1], nb) + pair_sum(na[2], nb)
        return pair_sum(na, nb[1]) + pair_sum(na, nb[2])

    def chain_distance(a, b):
        if linkage == "single":
            return _cluster_distance(members[a], members[b], pts, linkage, dist)
        return pair_sum(trees[a], trees[b]) / (sizes[a] * sizes[b])

    while active_count > 1:
        if not chain:
            for i in range(n):
                if active[i]:
                    chain.append(i)
                    break
        a = chain[-1]
        best = -1
        best_d = float("inf")
        for b in range(n):
            if b == a or not active[b]:
                continue
            d = chain_distance(a, b)
            if d < best_d:
                best_d = d
                best = b
        if len(chain) >= 2 and best == chain[-2]:
            b = best
            if b < a:
                a, b = b, a
            merges.append((a, b, best_d, len(members[a]) + len(members[b])))
            members[a] = members[a] + members[b]
            trees[a] = ("node", trees[a], trees[b], len(merges) - 1)
            sizes[a] += sizes[b]
            active[b] = False
            active_count -= 1
            chain.pop()
            chain.pop()
        else:
            chain.append(best)

    return merges
