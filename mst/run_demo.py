"""运行示例：连通/不连通/等权三种场景，打印两种算法的边集合与总权值。"""

from mst import kruskal_msf, prim_msf


def show(title: str, n: int, edges: list[tuple[int, int, int]]) -> None:
    print(f"== {title} ==")
    print("输入边 (u, v, w):", edges)
    rk, rp = kruskal_msf(n, edges), prim_msf(n, edges)
    assert rk.edges == rp.edges and rk.total_weight == rp.total_weight
    print(f"  所选边: {rk.edges}")
    print(f"  总权值: {rk.total_weight}")
    print(f"  连通分量(树棵数): {rk.components}，边数 = n - components = {n - rk.components}")
    print()


show("连通图（含自环、重边、等权）", 5,
     [(0, 1, 4), (0, 2, 4), (1, 2, 2), (1, 3, 5),
      (2, 3, 5), (3, 4, 3), (2, 4, 4), (4, 4, 9), (0, 1, 4)])

show("不连通图（按森林语义返回）", 6,
     [(0, 1, 1), (1, 2, 2), (0, 2, 2), (3, 4, 7), (5, 5, 9)])

show("所有边等权（由输入下标打破平局）", 4,
     [(0, 2, 4), (0, 1, 4), (1, 2, 4), (2, 3, 4), (0, 3, 4)])

show("单点图", 1, [])
