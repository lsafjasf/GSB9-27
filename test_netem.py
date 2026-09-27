"""netem 自测：可复现性 + 示例协议在 10% 丢包下的正确性 + 注入行为单测。

运行：python3 -m unittest -v   或   python3 test_netem.py
"""
import unittest

from netem import Channel, LinkParams, Segment, Simulator
from reliable_protocol import build_transfer

LOSSY_10 = LinkParams(loss_rate=0.10, delay=0.02, jitter=0.01,
                      reorder_rate=0.10, reorder_delay=0.06,
                      duplicate_rate=0.05, bandwidth=200_000)


def run_transfer(seed, n=60, params=LOSSY_10, timeout=0.3):
    payloads = [f"msg-{i:04d}" for i in range(n)]
    sim, sender, receiver, ch_ab, ch_ba = build_transfer(
        seed, [Segment(0.0, params)], [Segment(0.0, params)],
        payloads, timeout=timeout)
    sender.start()
    sim.run(until=600.0)  # 仿真时间上限，防止协议卡死时测试挂起
    return sim, sender, receiver, ch_ab, ch_ba, payloads


class TestReproducibility(unittest.TestCase):
    def test_same_seed_same_result(self):
        """相同种子 + 相同消息序列 => 交付序列与统计完全一致。"""
        r1 = run_transfer(seed=42)
        r2 = run_transfer(seed=42)
        self.assertEqual(r1[2].received, r2[2].received)
        self.assertEqual(r1[3].stats.snapshot(), r2[3].stats.snapshot())
        self.assertEqual(r1[4].stats.snapshot(), r2[4].stats.snapshot())
        self.assertEqual(r1[0].now, r2[0].now)
        self.assertEqual(r1[1].retransmissions, r2[1].retransmissions)

    def test_different_seed_differs(self):
        r1 = run_transfer(seed=1)
        r2 = run_transfer(seed=2)
        self.assertNotEqual(r1[3].stats.snapshot(), r2[3].stats.snapshot())

    def test_cross_process_determinism(self):
        """通道 RNG 不依赖 PYTHONHASHSEED：同种子跨构造顺序结果一致。"""
        # 以不同构造顺序建同样的通道，统计应一致
        def build(order):
            sim = Simulator()
            chs = {}
            for name in order:
                chs[name] = Channel(sim, name, [Segment(0.0, LOSSY_10)], seed=7)
            got = {name: [] for name in order}
            for name, ch in chs.items():
                ch.connect(lambda m, n=name: got[n].append(m))
            for i in range(200):
                for name, ch in chs.items():
                    ch.send(f"{name}-{i}")
            sim.run()
            return {n: chs[n].stats.snapshot() for n in order}, got

        snap_a, got_a = build(["x", "y"])
        snap_b, got_b = build(["y", "x"])
        self.assertEqual(snap_a, snap_b)
        self.assertEqual(got_a, got_b)


class TestProtocolUnderLoss(unittest.TestCase):
    def test_completes_under_10pct_loss(self):
        """10% 丢包（双向）+ 乱序 + 重复 + 带宽限制下，数据完整按序交付。"""
        sim, sender, receiver, ch_ab, ch_ba, payloads = run_transfer(seed=42)
        self.assertTrue(sender.done, "传输未在时限内完成")
        self.assertEqual(receiver.received, payloads)  # 不丢、不重、按序
        # 确实经历了丢包与重传，而非「网络很好」
        self.assertGreater(ch_ab.stats.dropped + ch_ba.stats.dropped, 0)
        self.assertGreater(sender.retransmissions, 0)
        # 实际丢包率应在 10% 附近（统计涨落容忍区间）
        total = ch_ab.stats.sent + ch_ba.stats.sent
        dropped = ch_ab.stats.dropped + ch_ba.stats.dropped
        self.assertTrue(0.03 < dropped / total < 0.20,
                        f"实际丢包率异常: {dropped}/{total}")

    def test_multiple_seeds_all_complete(self):
        for seed in range(10):
            sim, sender, receiver, _, _, payloads = run_transfer(seed=seed)
            self.assertTrue(sender.done, f"seed={seed} 未完成")
            self.assertEqual(receiver.received, payloads, f"seed={seed} 数据不符")


class TestInjectionBehaviors(unittest.TestCase):
    def _collect(self, segments, n=100, seed=0, sizes=None):
        sim = Simulator()
        ch = Channel(sim, "c", segments, seed=seed)
        got = []
        ch.connect(got.append)
        for i in range(n):
            ch.send(i, size=(sizes[i] if sizes else 1))
        sim.run()
        return ch, got, sim

    def test_clean_link(self):
        ch, got, _ = self._collect([Segment(0.0, LinkParams(delay=0.01))])
        self.assertEqual(ch.stats.dropped, 0)
        self.assertEqual(ch.stats.reordered, 0)
        self.assertEqual(got, list(range(100)))
        self.assertAlmostEqual(ch.stats.summary()["delay_avg"], 0.01)

    def test_total_loss(self):
        ch, got, _ = self._collect([Segment(0.0, LinkParams(loss_rate=1.0))])
        self.assertEqual(got, [])
        self.assertEqual(ch.stats.dropped, 100)

    def test_time_segments_switch(self):
        """前半段 100% 丢包，后半段无损：只有后半段的报文送达。"""
        segs = [Segment(0.0, LinkParams(loss_rate=1.0), end=5.0),
                Segment(5.0, LinkParams(delay=0.001))]
        sim = Simulator()
        ch = Channel(sim, "seg", segs, seed=0)
        got = []
        ch.connect(got.append)
        for i in range(10):
            sim.call_at(float(i), (lambda m: lambda: ch.send(m))(i))
        sim.run()
        self.assertEqual(got, [5, 6, 7, 8, 9])
        self.assertEqual(ch.stats.dropped, 5)
        self.assertEqual(ch.params_at(0.0).loss_rate, 1.0)
        self.assertEqual(ch.params_at(6.0).loss_rate, 0.0)

    def test_bandwidth_serializes(self):
        """带宽受限时，报文离开链路的间隔 >= size/bandwidth。"""
        arrivals = []
        sim = Simulator()
        ch = Channel(sim, "bw", [Segment(0.0, LinkParams(
            delay=0.0, bandwidth=1000.0))], seed=0)
        ch.connect(lambda m: arrivals.append(sim.now))
        for i in range(5):
            ch.send(i, size=100)  # 每个报文占用 0.1s 链路时间
        sim.run()
        for a, b in zip(arrivals, arrivals[1:]):
            self.assertAlmostEqual(b - a, 0.1)

    def test_reorder_and_duplicate_observed(self):
        params = LinkParams(delay=0.01, reorder_rate=0.5, reorder_delay=0.2,
                            duplicate_rate=0.5)
        ch, got, _ = self._collect([Segment(0.0, params)], n=200, seed=3)
        self.assertGreater(ch.stats.reordered, 0)
        self.assertGreater(ch.stats.duplicates_injected, 0)
        self.assertGreater(ch.stats.duplicate_deliveries, 0)
        # 首次送达集合仍恰好是全部报文
        self.assertEqual(ch.stats.delivered, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
