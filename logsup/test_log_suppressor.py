"""log_suppressor 自测：抑制正确性、新模板立即放行、内存上界、信息保留。"""

import sys
import unittest

from log_suppressor import LogSuppressor, extract_template


class TestTemplateExtraction(unittest.TestCase):
    def test_params_replaced(self):
        tpl, params = extract_template('db query failed ip=10.0.0.1:5432 cost=12.5ms sql="SELECT 1"')
        self.assertNotIn("10.0.0.1", tpl)
        self.assertNotIn("12.5", tpl)
        self.assertIn("10.0.0.1:5432", params)
        self.assertTrue(any(p.startswith("12.5") for p in params))

    def test_same_shape_same_template(self):
        t1, _ = extract_template("timeout after 3000 ms on host 10.1.1.1")
        t2, _ = extract_template("timeout after 9999 ms on host 172.16.0.2")
        self.assertEqual(t1, t2)


class TestFirstOccurrenceImmediatelyEmitted(unittest.TestCase):
    """核心要求：新模板必须立即放行，不能被已有抑制窗口吞掉。"""

    def test_new_template_during_active_suppression_window(self):
        sup = LogSuppressor(interval=60.0)
        t = 1000.0
        # 模板 A 进入抑制期
        self.assertEqual(sup.process("disk full on /dev/sda1", now=t),
                         ["disk full on /dev/sda1"])
        self.assertEqual(sup.process("disk full on /dev/sda2", now=t + 1), [])  # 被抑制
        # 窗口中途出现全新错误模式 B：必须立刻全量输出
        out = sup.process("OOM killer invoked on pid 4242", now=t + 2)
        self.assertEqual(out, ["OOM killer invoked on pid 4242"])
        # B 的第二次出现才被抑制
        self.assertEqual(sup.process("OOM killer invoked on pid 9999", now=t + 3), [])

    def test_every_distinct_template_first_line_kept(self):
        sup = LogSuppressor(interval=3600.0)  # 超长窗口
        kept = 0
        for i in range(5000):
            v = i % 100
            word = chr(97 + v // 26) + chr(97 + v % 26)  # 100 个不同字母词
            out = sup.process(f"error {word} detail {i}", now=0.0)
            kept += len(out)
        # 100 个不同模板 => 恰好 100 条首条被保留
        self.assertEqual(kept, 100)


class TestSuppressionAndSummary(unittest.TestCase):
    def test_burst_collapsed_to_periodic_summary(self):
        sup = LogSuppressor(interval=10.0)
        out_total = []
        t = 0.0
        # 首条
        out_total += sup.process("conn reset by peer 10.0.0.1:80", now=t)
        # 10 秒内 9999 条重复，全部抑制
        for i in range(9999):
            t += 0.001
            out_total += sup.process(f"conn reset by peer 10.0.0.{i % 250}:80", now=t)
        self.assertEqual(len(out_total), 1)
        # 跨过间隔 -> 一条汇总
        t += 10.0
        out_total += sup.process("conn reset by peer 10.0.0.7:80", now=t)
        self.assertEqual(len(out_total), 2)
        summary = out_total[-1]
        self.assertIn("重复 10000 次", summary)  # 9999 + 触发汇总的这条
        self.assertIn("过去", summary)
        self.assertIn("秒", summary)

    def test_summary_carries_param_distribution(self):
        sup = LogSuppressor(interval=5.0)
        sup.process("login failed user=100 reason=1", now=0.0)
        for i in range(50):
            sup.process(f"login failed user={100 + (i % 2)} reason={i % 3}", now=1.0)
        out = sup.process("login failed user=100 reason=1", now=6.0)
        self.assertEqual(len(out), 1)
        self.assertIn("参数分布", out[0])
        self.assertIn("p0=", out[0])  # user 参数分布
        self.assertIn("×", out[0])    # 取值×次数

    def test_no_summary_before_interval(self):
        sup = LogSuppressor(interval=10.0)
        sup.process("x 1", now=0.0)
        for k in range(100):
            self.assertEqual(sup.process(f"x {k}", now=9.9), [])


class TestMemoryBound(unittest.TestCase):
    def test_template_count_hard_capped(self):
        cap = 1000
        sup = LogSuppressor(interval=60.0, max_templates=cap)
        for i in range(200_000):  # 海量不同模板风暴（字母部分各不相同）
            n, letters = i, ""
            while True:
                letters = chr(97 + n % 26) + letters
                n = n // 26 - 1
                if n < 0:
                    break
            sup.process(f"unique error {letters} happened", now=float(i))
        self.assertLessEqual(sup.tracked_templates, cap)
        self.assertEqual(sup.evicted, 200_000 - cap)

    def test_state_footprint_bounded(self):
        """抑制器状态的字节量与输入量解耦：灌入 20 万条后状态仍为小常数级。"""
        cap = 500
        sup = LogSuppressor(interval=60.0, max_templates=cap)
        for i in range(200_000):
            sup.process(f"err kind={i % 50} payload={i}", now=float(i))
        # 粗粒度估计：模板表本身 + 每个模板状态对象
        approx = sys.getsizeof(sup._states)
        for state in sup._states.values():
            approx += sys.getsizeof(state) + sys.getsizeof(state.template)
            for dist in state.param_dist:
                approx += sys.getsizeof(dist)
        self.assertLess(approx, 5 * 1024 * 1024)  # 远小于 5MB，且与 20 万输入解耦

    def test_param_distribution_capped(self):
        sup = LogSuppressor(interval=3600.0, max_distinct_per_param=8)
        sup.process("req path=0", now=0.0)
        for i in range(10_000):
            sup.process(f"req path={i}", now=1.0)  # 1 万个不同参数值
        state = next(iter(sup._states.values()))
        # 每个参数位置最多 8 个真实取值 + 1 个 <other> 桶
        self.assertLessEqual(len(state.param_dist[0]), 9)

    def test_eviction_flushes_final_summary(self):
        sup = LogSuppressor(interval=3600.0, max_templates=2)
        sup.process("alpha 1", now=0.0)
        sup.process("alpha 2", now=1.0)          # alpha 被抑制 1 次
        sup.process("beta 1", now=2.0)
        out = sup.process("gamma 1", now=3.0)    # 触发淘汰最久未活动的 alpha
        # 输出 = alpha 的最终汇总 + gamma 首条
        self.assertEqual(len(out), 2)
        self.assertIn("SUPPRESSED-FINAL", out[0])
        self.assertIn("重复 1 次", out[0])
        self.assertEqual(out[1], "gamma 1")


class TestInfoRetentionUnderBurst(unittest.TestCase):
    """突发场景量化：抑制前后写入量、关键信息保留。"""

    def test_fault_storm_metrics(self):
        sup = LogSuppressor(interval=10.0, max_templates=10_000)
        outputs = []
        t = 0.0
        # 场景：30 秒故障，1 个高频模板每秒 2 万条，夹杂 3 种新错误模式
        hot = "disk io error on /dev/sda sector 8"
        rare_templates = [
            "raid controller degraded slot 3",
            "filesystem remounted read-only",
            "kernel hung task sync blocked 120",
        ]
        for sec in range(30):
            for _ in range(20_000):
                outputs += sup.process(hot, now=t)
            for rare in rare_templates:  # 每种新模式每秒各出现 1 次
                outputs += sup.process(rare, now=t)
            t += 1.0
        outputs += sup.flush(now=t)

        stats = sup.stats()
        # 1) 写入量：60 万+90 条输入 -> 首条 4 条 + 每模板每 10 秒一条汇总
        self.assertEqual(stats["received"], 600_090)
        self.assertLess(stats["emitted"], 100)
        self.assertGreater(stats["suppression_rate"], 0.999)
        self.assertGreater(stats["byte_reduction"], 0.99)
        # 2) 关键信息保留：3 种罕见新模式的首条全部在输出里
        for rare in rare_templates:
            self.assertIn(rare, outputs)
        self.assertIn(hot, outputs)
        # 3) 汇总确实携带重复次数与参数分布
        summaries = [l for l in outputs if l.startswith("[SUPPRESSED")]
        self.assertTrue(any("重复" in s and "参数分布" in s for s in summaries))


if __name__ == "__main__":
    unittest.main(verbosity=2)
