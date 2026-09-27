"""Cross-check hclust.py (fast NN-chain) against naive_hclust.py.

Usage: python3 crosscheck.py [-v]

Two comparison modes:

  * random continuous data  -> fast vs naive_global
    (ties have probability zero, so the merge order is well defined and
     both algorithms must produce the identical hierarchy)

  * adversarial tie-heavy data -> fast vs naive_chain
    (naive_chain mirrors the fast algorithm's tie-breaking but recomputes
     every distance from the raw points, so the Lance-Williams updates and
     condensed-matrix indexing are validated exactly where ties make them
     most error-prone)

A merge is compared as (leaf set A, leaf set B, distance): independent of
internal cluster numbering.  Distances are compared with a small relative
tolerance because Lance-Williams and direct summation round differently.
"""

import random
import sys
import time

from hclust import hierarchical_clustering, cut_tree
from naive_hclust import naive_global, naive_chain

TOL = 1e-9


def leaf_sets(n, merges, get_ids):
    """Reconstruct (frozenset, frozenset, distance) per merge.

    get_ids(m) -> (id_a, id_b, distance); ids 0..n-1 are the points,
    n+i is the cluster created by merge i.
    """
    members = {i: frozenset([i]) for i in range(n)}
    out = []
    for step, m in enumerate(merges):
        a, b, d = get_ids(m)
        ma, mb = members[a], members[b]
        if min(ma) > min(mb):
            ma, mb = mb, ma
        out.append((ma, mb, d))
        members[n + step] = ma | mb
    return out


def normalize_fast(n, merges):
    return leaf_sets(n, merges, lambda m: (m[0], m[1], m[2]))


def normalize_naive_chain(n, merges):
    # naive_chain records merges between root ids with in-place absorption
    # (cluster b folded into cluster a), like the main loop of hclust.py.
    # Sort stably by distance (as the library does) and replay the
    # absorption to recover leaf sets.
    members = [frozenset([i]) for i in range(n)]
    out = []
    for a, b, d, _size in sorted(merges, key=lambda m: m[2]):
        ma, mb = members[a], members[b]
        x, y = (ma, mb) if min(ma) < min(mb) else (mb, ma)
        out.append((x, y, d))
        members[a] = ma | mb
        members[b] = None
    return out


def normalize_naive_global(n, merges):
    # naive_global identifies clusters by position in its shrinking list;
    # mirror those list operations to recover leaf sets.  Its natural
    # emission order is the global-argmin order, which coincides with the
    # distance-sorted order whenever merge heights are distinct.
    clusters = [frozenset([i]) for i in range(n)]
    out = []
    for i, j, d, _size in merges:
        ma, mb = clusters[i], clusters[j]
        a, b = (ma, mb) if min(ma) < min(mb) else (mb, ma)
        out.append((a, b, d))
        clusters[i] = ma | mb
        del clusters[j]
    return out


def normalize(n, merges, kind):
    if kind == "fast":
        return normalize_fast(n, merges)
    if kind == "chain":
        return normalize_naive_chain(n, merges)
    return normalize_naive_global(n, merges)


def assert_same_hierarchy(n, fast, naive, label):
    f, g = normalize_fast(n, fast), naive
    assert len(f) == len(g), "%s: %d vs %d merges" % (label, len(f), len(g))
    for step, ((fa, fb, fd), (ga, gb, gd)) in enumerate(zip(f, g)):
        assert fa == ga and fb == gb, (
            "%s: merge %d differs\n fast  %s + %s\n naive %s + %s"
            % (label, step, sorted(fa), sorted(fb), sorted(ga), sorted(gb)))
        scale = max(1.0, abs(fd), abs(gd))
        assert abs(fd - gd) <= TOL * scale, (
            "%s: merge %d distance %.17g vs %.17g" % (label, step, fd, gd))


def check_monotone(merges, label):
    for i in range(1, len(merges)):
        assert merges[i][2] >= merges[i - 1][2] - 1e-12, (
            "%s: distances not monotone at step %d" % (label, i))


def random_points(rng, n, dim):
    return [[rng.random() * 100 for _ in range(dim)] for _ in range(n)]


def main():
    verbose = "-v" in sys.argv
    t0 = time.time()
    rng = random.Random(20260928)
    cases = 0

    # ---- 1. random data, fast vs naive_global, both linkages -------------
    for trial in range(60):
        n = rng.randint(2, 40)
        dim = rng.randint(1, 5)
        pts = random_points(rng, n, dim)
        for linkage in ("single", "average"):
            fast = hierarchical_clustering(pts, linkage)
            naive = naive_global(pts, linkage)
            assert_same_hierarchy(n, fast, normalize(n, naive, "global"),
                                  "random#%d/%s" % (trial, linkage))
            check_monotone(fast, "random#%d/%s" % (trial, linkage))
            cases += 1
    if verbose:
        print("random-data cases ok")

    # ---- 2. adversarial ties, fast vs naive_chain -------------------------
    adversarial = []
    adversarial.append(("all-identical-1d", [[5.0]] * 12))
    adversarial.append(("all-identical-3d", [[1.0, 2.0, 3.0]] * 9))
    adversarial.append(("two-points", [[0.0], [3.0]]))
    adversarial.append(("duplicates-mixed",
                        [[0.0], [0.0], [0.0], [2.0], [2.0], [5.0]]))
    adversarial.append(("grid-4x4", [[float(x), float(y)]
                                     for x in range(4) for y in range(4)]))
    adversarial.append(("equidistant-ring",
                        [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0], [0.0, -1.0]]))
    adversarial.append(("tied-pair-distances",
                        [[0.0], [1.0], [2.0], [10.0], [11.0], [12.0]]))
    adversarial.append(("integers-1d", [[float(i % 4)] for i in range(16)]))
    for name, pts in adversarial:
        for linkage in ("single", "average"):
            fast = hierarchical_clustering(pts, linkage)
            naive = naive_chain(pts, linkage)
            assert_same_hierarchy(len(pts), fast, normalize(n, naive, "chain"),
                                  "%s/%s" % (name, linkage))
            check_monotone(fast, "%s/%s" % (name, linkage))
            cases += 1
    if verbose:
        print("adversarial-tie cases ok")

    # ---- 3. targeted invariants -------------------------------------------
    # all points identical -> every merge distance is exactly 0
    for linkage in ("single", "average"):
        z = hierarchical_clustering([[7.0, 7.0]] * 6, linkage)
        assert len(z) == 5 and all(m.distance == 0.0 for m in z), linkage
        cases += 1
    # single point / empty input -> no merges
    assert hierarchical_clustering([[1.0, 2.0]], "single") == []
    assert hierarchical_clustering([], "average") == []
    # two points -> one merge at their distance, for both linkages
    for linkage in ("single", "average"):
        z = hierarchical_clustering([[0.0], [3.0], [4.0]], linkage)
        assert z[0].distance == 1.0, linkage
        cases += 1
    # hand-computed average linkage (UPGMA) on 1D points 0, 1, 4, 5:
    # merge (0,1)@1, (4,5)@1, then d = (|0-4|+|0-5|+|1-4|+|1-5|)/4 = 4
    z = hierarchical_clustering([[0.0], [1.0], [4.0], [5.0]], "average")
    assert [m.distance for m in z] == [1.0, 1.0, 4.0], z
    # cut_tree: k clusters and threshold cuts agree with the merge history
    labels = cut_tree(z, 4, n_clusters=2)
    assert labels == [0, 0, 1, 1], labels
    labels = cut_tree(z, 4, threshold=1.0)
    assert labels == [0, 0, 1, 1], labels
    labels = cut_tree(z, 4, threshold=0.5)
    assert labels == [0, 1, 2, 3], labels
    cases += 5
    if verbose:
        print("targeted invariants ok")

    print("OK: %d checks passed in %.2fs" % (cases, time.time() - t0))


if __name__ == "__main__":
    main()
