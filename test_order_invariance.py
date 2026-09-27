"""顺序无关性测试：打乱点序与边序后，规范化结果必须逐字节相等。

对比方法：scc.canonical 将分量内点排序、分量间按字典序排序、
压缩图边按规范编号排序，输出确定性的 (components, dag_edges)，
多次运行 / 任意输入顺序下可直接用 == 比较。
"""
import random

from scc import solve


def random_graph(rng, n, m):
    nodes = list(range(n))
    edges = [(rng.randrange(n), rng.randrange(n)) for _ in range(m)]
    return nodes, edges


def main():
    rng = random.Random(7)
    cases = 0
    for trial in range(200):
        n = rng.randint(1, 60)
        m = rng.randint(0, 4 * n)
        nodes, edges = random_graph(rng, n, m)
        baseline = solve(nodes, edges)
        for _ in range(5):
            shuffled_nodes = nodes[:]
            shuffled_edges = edges[:]
            rng.shuffle(shuffled_nodes)
            rng.shuffle(shuffled_edges)
            got = solve(shuffled_nodes, shuffled_edges)
            assert got == baseline, (
                f"顺序无关性失败 trial={trial}\n{got}\n{baseline}")
        cases += 1
    # 大一点的结构化图：环 + 链 + 分层
    nodes = list(range(5000))
    edges = ([(i, i + 1) for i in range(4999)]            # 长链
             + [(i + 1, i) for i in range(0, 2000, 2)]    # 若干 2-环
             + [(i, (i * 7) % 5000) for i in range(0, 5000, 3)])
    baseline = solve(nodes, edges)
    for _ in range(3):
        sn, se = nodes[:], edges[:]
        rng.shuffle(sn)
        rng.shuffle(se)
        assert solve(sn, se) == baseline, "结构化大图顺序无关性失败"
    print(f"OK: {cases} 个随机图 + 1 个 5000 点结构化图，"
          f"打乱点/边顺序后结果完全一致")


if __name__ == "__main__":
    main()
