"""log_suppressor 单元测试：python3 -m unittest -v"""

import unittest

from log_suppressor import LogSuppressor, extract_template


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TemplateTest(unittest.TestCase):
    def test_numbers_and_ips_become_placeholders(self):
        template, params = extract_template("connect to 10.0.0.7:5432 timeout after 30s")
        self.assertEqual(template, "connect to * timeout after *s")
        self.assertEqual(params, ("10.0.0.7:5432", "30"))

    def test_same_shape_same_template(self):
        t1, _ = extract_template('user "alice" login from 192.168.1.1')
        t2, _ = extract_template('user "bob" login from 192.168.1.2')
        self.assertEqual(t1, t2)


class SuppressionTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.sup = LogSuppressor(interval=10.0, max_templates=100, time_fn=self.clock)

    def test_first_occurrence_passes_immediately(self):
        """新模板必须立即放行，不能被抑制窗口吞掉。"""
        out = self.sup.process("disk /dev/sda io error code 5")
        self.assertEqual(out, ["disk /dev/sda io error code 5"])
        # 紧接着一个全新模板，即使在别人窗口期内也必须放行
        out = self.sup.process("auth service unreachable")
        self.assertEqual(out, ["auth service unreachable"])

    def test_new_template_passes_even_under_heavy_suppression(self):
        # 灌入大量被抑制的重复日志
        for i in range(1000):
            self.sup.process(f"heartbeat ok latency {i}ms")
        # 全新错误模式第一次出现，必须原样立即输出
        out = self.sup.process("FATAL data corruption on block 998")
        self.assertEqual(out, ["FATAL data corruption on block 998"])

    def test_repeats_suppressed_then_summary(self):
        self.assertEqual(self.sup.process("job 1 failed"), ["job 1 failed"])
        for i in range(2, 102):
            self.assertEqual(self.sup.process(f"job {i} failed"), [])
        self.clock.advance(10.0)
        out = self.sup.process("job 102 failed")
        self.assertEqual(len(out), 2)
        summary = out[0]
        self.assertIn("过去 10.0 秒重复 100 次", summary)
        self.assertIn("template='job * failed'", summary)
        self.assertEqual(out[1], "job 102 failed")  # 新窗口首条全量放行

    def test_summary_contains_param_distribution(self):
        self.sup.process('req user "alice" status 500')
        self.sup.process('req user "bob" status 500')
        self.sup.process('req user "alice" status 404')
        self.clock.advance(10.0)
        out = self.sup.process('req user "carol" status 500')
        summary = out[0]
        self.assertIn("重复 2 次", summary)
        # 参数分布摘要保留被抑制样本的信息
        self.assertIn("arg0=", summary)
        self.assertIn("alice", summary)
        self.assertIn("bob", summary)
        self.assertIn("arg1=", summary)
        self.assertIn("500", summary)
        self.assertIn("404", summary)

    def test_flush_emits_pending_summaries(self):
        self.sup.process("x 1 happened")
        self.sup.process("x 2 happened")
        self.sup.process("x 3 happened")
        out = self.sup.flush()
        self.assertEqual(len(out), 1)
        self.assertIn("重复 2 次", out[0])
        self.assertEqual(self.sup.tracked_templates, 0)


class MemoryBoundTest(unittest.TestCase):
    def test_template_count_hard_bound(self):
        """海量不同模板下，抑制器状态必须有上界。"""
        clock = FakeClock()
        sup = LogSuppressor(interval=60.0, max_templates=100, time_fn=clock)
        for i in range(100_000):
            sup.process(f"unique error variant{i} detail {i * 7}")  # variantN 不归一化 => 10 万不同模板
        self.assertLessEqual(sup.tracked_templates, 100)
        self.assertEqual(sup.evicted, 100_000 - 100)

    def test_lru_evicts_coldest_template(self):
        clock = FakeClock()
        sup = LogSuppressor(interval=60.0, max_templates=3, time_fn=clock)
        sup.process("alpha 1")
        sup.process("beta 2")
        sup.process("gamma 3")
        sup.process("alpha 9")          # alpha 变热
        sup.process("delta 4")          # 触发淘汰，应淘汰最冷的 beta
        templates = set(sup._states.keys())
        self.assertIn("alpha *", templates)
        self.assertIn("gamma *", templates)
        self.assertIn("delta *", templates)
        self.assertNotIn("beta *", templates)

    def test_eviction_flushes_pending_summary(self):
        """被淘汰的模板若有未发出汇总，必须冲刷而不是丢失。"""
        clock = FakeClock()
        sup = LogSuppressor(interval=60.0, max_templates=1, time_fn=clock)
        sup.process("hot 1")
        sup.process("hot 2")            # 被抑制，suppressed=1
        sup.process("hot 3")            # 被抑制，suppressed=2
        out = sup.process("cold 9")     # 新模板挤掉 hot，应带出汇总
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], "cold 9")
        self.assertIn("重复 2 次", out[1])

    def test_samples_bounded_per_template(self):
        clock = FakeClock()
        sup = LogSuppressor(interval=60.0, max_templates=10, max_samples=8, time_fn=clock)
        sup.process("evt 0")
        for i in range(1, 10_000):
            sup.process(f"evt {i}")
        state = next(iter(sup._states.values()))
        self.assertLessEqual(len(state.samples), 8)
        self.assertEqual(state.suppressed, 9_999)


if __name__ == "__main__":
    unittest.main()
