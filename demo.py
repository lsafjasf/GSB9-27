"""demo.py -- 小例子：输出合并历史（JSON 样例）、文本树状图、按阈值切分。

运行：python3 demo.py
"""

import json

from hclust import linkage, cut

# 二维点：三个明显的簇 {0,1,2} {3,4} {5,6,7}
POINTS = [
    (0.0, 0.0),   # 0
    (1.0, 0.2),   # 1
    (0.3, 1.0),   # 2
    (10.0, 0.0),  # 3
    (10.5, 0.8),  # 4
    (0.0, 10.0),  # 5
    (0.8, 10.4),  # 6
    (0.2, 11.0),  # 7
]


def ascii_dendrogram(merges, n):
    """简单的文本树状图（按合并历史递归打印）。"""
    children = {n + k: (m.id1, m.id2, m.distance) for k, m in enumerate(merges)}
    lines = []

    def walk(cid, depth):
        if cid < n:
            lines.append("  " * depth + "点%d" % cid)
        else:
            a, b, d = children[cid]
            lines.append("  " * depth + "┐ 距离=%.3f" % d)
            walk(a, depth + 1)
            walk(b, depth + 1)

    walk(2 * n - 2, 0)
    return "\n".join(lines)


def main():
    n = len(POINTS)
    for method in ("single", "average"):
        merges = linkage(POINTS, method)
        print("== method=%s ==" % method)
        print("步  簇A   簇B   距离        新簇大小")
        for k, m in enumerate(merges):
            print("%2d  %4d %5d   %.6f   %d" % (k, m.id1, m.id2, m.distance, m.size))
        print()
        print(ascii_dendrogram(merges, n))
        print()
        if method == "average":
            # 合并历史样例：scipy linkage 兼容格式，可直接用于画树状图
            sample = {
                "method": method,
                "n_points": n,
                "points": [list(p) for p in POINTS],
                "merges": [
                    {"step": k, "cluster1": m.id1, "cluster2": m.id2,
                     "distance": round(m.distance, 6), "size": m.size}
                    for k, m in enumerate(merges)
                ],
            }
            with open("examples/merge_history_sample.json", "w") as f:
                json.dump(sample, f, ensure_ascii=False, indent=2)
            # 切分示例：在最大距离跳跃处切开
            gaps = [(merges[k + 1].distance - merges[k].distance, k)
                    for k in range(len(merges) - 1)]
            gap, k = max(gaps)
            threshold = (merges[k].distance + merges[k + 1].distance) / 2
            labels = cut(merges, n, threshold)
            print("最大距离跳跃: 第%d步 %.3f -> 第%d步 %.3f，取阈值 %.3f"
                  % (k, merges[k].distance, k + 1, merges[k + 1].distance, threshold))
            print("切分结果（每个点的簇标签）:", labels)
            print("簇数量:", len(set(labels))
            )
            print()


if __name__ == "__main__":
    main()
