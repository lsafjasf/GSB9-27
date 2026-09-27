"""Small end-to-end example: merge history, cuts, ASCII dendrogram."""

from hclust import hierarchical_clustering, cut_tree

# two blobs and an outlier pair
POINTS = [
    (0.0, 0.0), (0.2, 0.1), (0.1, 0.3), (0.3, 0.2),   # blob A
    (5.0, 5.0), (5.2, 4.9), (4.9, 5.2),               # blob B
    (10.0, 0.0), (10.1, 0.2),                         # outlier pair
]


def dendrogram(merges, n, labels=None, width=48):
    """Render a horizontal ASCII dendrogram (leaves top-to-bottom)."""
    children = {n + s: (m.id_a, m.id_b) for s, m in enumerate(merges)}
    order = []

    def walk(c):
        if c < n:
            order.append(c)
        else:
            walk(children[c][0])
            walk(children[c][1])

    walk(2 * n - 2)
    row_of = {leaf: r for r, leaf in enumerate(order)}
    span = {i: (row_of[i], row_of[i]) for i in range(n)}
    mid = {i: row_of[i] for i in range(n)}
    col = {i: 0 for i in range(n)}
    max_d = merges[-1].distance or 1.0
    grid = [[" "] * (width + 1) for _ in range(len(order))]
    for step, m in enumerate(merges):
        cid = n + step
        c = max(1, round(m.distance / max_d * width))
        r0 = min(span[m.id_a][0], span[m.id_b][0])
        r1 = max(span[m.id_a][1], span[m.id_b][1])
        span[cid] = (r0, r1)
        mid[cid] = (mid[m.id_a] + mid[m.id_b]) // 2
        for child in (m.id_a, m.id_b):
            r = mid[child]
            for x in range(col[child], c):
                grid[r][x] = "-"
            grid[r][c] = "+"
        for r in range(r0, r1 + 1):
            if grid[r][c] == " ":
                grid[r][c] = "|"
        col[cid] = c
    lines = []
    for r, leaf in enumerate(order):
        label = labels[leaf] if labels else str(leaf)
        lines.append("%-10s %s" % (label, "".join(grid[r]).rstrip()))
    return "\n".join(lines)


def main():
    n = len(POINTS)
    for linkage in ("single", "average"):
        merges = hierarchical_clustering(POINTS, linkage)
        print("=== linkage: %s ===" % linkage)
        print("%5s %8s %8s %10s %6s" % ("step", "clust A", "clust B",
                                        "distance", "size"))
        for step, m in enumerate(merges):
            print("%5d %8d %8d %10.4f %6d" % (step, m.id_a, m.id_b,
                                              m.distance, m.size))
        for k in (2, 3):
            print("cut k=%d -> %s" % (k, cut_tree(merges, n, n_clusters=k)))
        big_jumps = [(i, merges[i].distance, merges[i + 1].distance)
                     for i in range(len(merges) - 1)
                     if merges[i + 1].distance > 2 * merges[i].distance + 1e-9]
        print("distance jumps (cut candidates):", big_jumps)
        labels = ["p%d %s" % (i, POINTS[i]) for i in range(n)]
        print(dendrogram(merges, n, labels))
        print()


if __name__ == "__main__":
    main()
