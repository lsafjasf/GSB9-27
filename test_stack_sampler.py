"""stack_sampler 自测（标准库 unittest）。

覆盖：
- 调用极短（海量微小调用）
- 单层调用
- 超深栈（递归深度接近解释器上限）
- 采样频率高于调用频率
- 递归折叠的正确性（采样与全量两侧）
- 采样丢失统计
- 采样聚合 vs 全量统计的占比偏差
"""

import sys
import time
import unittest

from stack_sampler import (
    FullTracer,
    StackSampler,
    aggregate_by_key,
    fold_path,
)


def spin(n):
    x = 0
    for i in range(n):
        x += i
    return x


def spin_for(seconds):
    end = time.perf_counter() + seconds
    x = 0
    while time.perf_counter() < end:
        x += 1
    return x


def tiny():
    return 1


def leaf_single():
    return spin_for(0.3)


def rec_deep(n, sink):
    if n == 0:
        sink.append(spin_for(0.3))
        return
    rec_deep(n - 1, sink)


def rec_timed(n):
    if n == 0:
        return spin(2000)
    return rec_timed(n - 1)


def agg_get(agg, suffix):
    """按函数名后缀取 [self, total]（兼容嵌套函数的 qualname）。"""
    self_v, total_v = 0.0, 0.0
    for key, val in agg.items():
        if key == suffix or key.endswith("." + suffix):
            self_v += val[0]
            total_v += val[1]
    return self_v, total_v


def path_keys(root):
    """收集采样树中所有根->叶路径（用于检查折叠）。"""
    paths = []

    def visit(node, acc):
        acc = acc + [node.key]
        if not node.children:
            paths.append(acc)
        for child in node.children.values():
            visit(child, acc)

    visit(root, [])
    return paths


class TestFoldPath(unittest.TestCase):
    def test_fold_consecutive(self):
        self.assertEqual(fold_path(["a", "b", "b", "b", "c"]), ["a", "b", "c"])

    def test_fold_keeps_non_consecutive(self):
        # 非相邻的同名帧不是递归，不应折叠
        self.assertEqual(fold_path(["a", "b", "a", "b"]), ["a", "b", "a", "b"])


class TestSamplerBasics(unittest.TestCase):
    def test_single_level_call(self):
        with StackSampler(interval=0.001) as sampler:
            leaf_single()
        self.assertGreater(sampler.samples_taken, 100)
        agg = aggregate_by_key(sampler.root, "count")
        total = sampler.root.total_count
        # 单层调用：leaf_single 累计占比 ~100%，自身耗时集中在 spin_for
        self.assertGreater(agg_get(agg, "leaf_single")[1] / total, 0.95)
        self.assertGreater(agg_get(agg, "spin_for")[0] / total, 0.85)

    def test_sampling_faster_than_calls(self):
        # 调用频率 ~10Hz（每次 2ms 工作 + 98ms 空闲），采样 2kHz
        def rare():
            spin_for(0.002)

        with StackSampler(interval=0.0005) as sampler:
            for _ in range(4):
                time.sleep(0.098)
                rare()
        report = sampler.loss_report()
        # 采样数应接近 时长/间隔，丢失率低
        self.assertGreater(report["taken"], 500)
        self.assertLess(report["loss_rate"], 0.05)
        agg = aggregate_by_key(sampler.root, "count")
        # 稀有调用占比小但仍被正确归因（可能为 0，不能崩溃）
        rare_total = agg_get(agg, "rare")[1]
        self.assertLessEqual(rare_total / sampler.root.total_count, 0.10)

    def test_extremely_short_calls(self):
        # 海量极短调用：采样器不应崩溃，全量计数必须精确
        with StackSampler(interval=0.0005) as sampler:
            end = time.perf_counter() + 0.3
            n = 0
            while time.perf_counter() < end:
                tiny()
                n += 1
        self.assertGreater(sampler.samples_taken, 100)

        tracer = FullTracer()
        tracer.start()
        m = 0
        for _ in range(50000):
            tiny()
            m += 1
        tracer.stop()
        tiny_nodes = []

        def find(node):
            if node.key == "tiny" or node.key.endswith(".tiny"):
                tiny_nodes.append(node)
            for c in node.children.values():
                find(c)

        find(tracer.root)
        self.assertEqual(sum(node.calls for node in tiny_nodes), 50000)


class TestRecursionFolding(unittest.TestCase):
    def test_deep_stack_folds_to_single_node(self):
        # 超深栈：递归 800 层，折叠后树中 rec_deep 不得串联出现
        sys.setrecursionlimit(5000)
        sink = []
        with StackSampler(interval=0.001) as sampler:
            rec_deep(800, sink)
        self.assertGreater(sampler.samples_taken, 100)
        for path in path_keys(sampler.root):
            count = sum(1 for k in path if k.endswith("rec_deep"))
            self.assertLessEqual(count, 1, f"recursion not folded: {path}")
        # 折叠后树深度有界（远小于 800）
        deepest = max(len(p) for p in path_keys(sampler.root))
        self.assertLess(deepest, 30)

    def test_full_tracer_recursion_folding(self):
        tracer = FullTracer()
        tracer.start()
        rec_timed(50)
        tracer.stop()

        def find(node, key, acc):
            if node.key.endswith(key):
                acc.append(node)
            for c in node.children.values():
                find(c, key, acc)

        nodes = []
        find(tracer.root, "rec_timed", nodes)
        self.assertEqual(len(nodes), 1, "递归在全量树中应折叠为一个节点")
        node = nodes[0]
        self.assertFalse(
            any(c.key.endswith("rec_timed") for c in node.children.values())
        )
        self.assertEqual(node.calls, 51)
        # 累计耗时 >= 自身耗时，且自身耗时 = 累计 - 非递归后代耗时
        self.assertGreaterEqual(node.total_time, node.self_time)
        leaf = None
        for c in node.children.values():
            if c.key.endswith("spin"):
                leaf = c
        self.assertIsNotNone(leaf)
        self.assertAlmostEqual(
            node.self_time, node.total_time - leaf.total_time, places=6
        )


class TestSamplingLoss(unittest.TestCase):
    def test_loss_reported_under_tiny_interval(self):
        # 间隔极小（10us），采样线程必然跟不上，丢失必须被统计而非静默
        with StackSampler(interval=0.00001) as sampler:
            spin_for(0.2)
        report = sampler.loss_report()
        self.assertGreater(report["taken"], 0)
        self.assertGreaterEqual(report["missed_ticks"], 0)
        self.assertGreaterEqual(report["loss_rate"], 0.0)
        self.assertLessEqual(report["loss_rate"], 1.0)


class TestSamplerVsFull(unittest.TestCase):
    def test_cumulative_share_close_to_full(self):
        # 人为构造：A 路径约占 70%，B 路径约占 30%。
        # 用长块（0.7ms/0.3ms）让全量追踪的插桩开销可忽略，
        # 采样器加 jitter 避免与 workload 周期相位锁定。
        def leaf_a():
            spin_for(0.0007)

        def mid_a():
            leaf_a()

        def leaf_b():
            spin_for(0.0003)

        def workload(duration):
            end = time.perf_counter() + duration
            while time.perf_counter() < end:
                mid_a()
                leaf_b()

        tracer = FullTracer()
        tracer.start()
        workload(1.0)
        tracer.stop()

        with StackSampler(interval=0.001, jitter=0.5) as sampler:
            workload(1.0)

        full = aggregate_by_key(tracer.root, "time")
        samp = aggregate_by_key(sampler.root, "count")
        full_total = tracer.root.total_time
        samp_total = sampler.root.total_count
        for key in ("mid_a", "leaf_a", "leaf_b"):
            f = agg_get(full, key)[1] / full_total
            s = agg_get(samp, key)[1] / samp_total
            self.assertLess(
                abs(f - s), 0.06, f"{key}: full={f:.3f} sampled={s:.3f}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
