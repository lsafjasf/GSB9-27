#!/usr/bin/env python3
"""test_failure_cluster.py — 框架自测（标准库 unittest）"""
import json
import os
import unittest

from failure_cluster import cluster_failures, evaluate, extract_features, normalize

BASE = os.path.dirname(os.path.abspath(__file__))


def sigs(messages):
    return [c.signature for c in cluster_failures(messages) for _ in c.members]


class NoiseSuppressionTest(unittest.TestCase):
    """随机序列号 / 内存地址 / 耗时数字不得影响分组。"""

    def test_serial_numbers(self):
        a = "AssertionError: expected status 200 but got 500 (request_id=req-100001, order_id=882371)"
        b = "AssertionError: expected status 200 but got 500 (request_id=req-999988, order_id=11235)"
        self.assertEqual(sigs([a, b])[0], sigs([a, b])[1])

    def test_memory_addresses(self):
        a = "TypeError: bad operand for <User object at 0x7f3a2b10c450>"
        b = "TypeError: bad operand for <User object at 0x00002b4c99d8>"
        self.assertEqual(len(cluster_failures([a, b])), 1)

    def test_durations(self):
        a = "TimeoutError: query exceeded 120ms (elapsed 0.12s)"
        b = "TimeoutError: query exceeded 98765ms (elapsed 98.765s)"
        self.assertEqual(len(cluster_failures([a, b])), 1)

    def test_uuid_and_timestamp(self):
        a = "KeyError: 'session' trace=3f2504e0-4f89-41d3-9a0c-0305e82c3301 at 2026-09-01 10:00:00"
        b = "KeyError: 'session' trace=8b0a1fa2-1234-4bcd-9abc-ff00ff00ff00 at 2026-09-28 23:59:59"
        self.assertEqual(len(cluster_failures([a, b])), 1)

    def test_normalize_idempotent(self):
        text = "Error: id=123456 addr=0xdeadbeef took 42ms"
        self.assertEqual(normalize(normalize(text)), normalize(text))

    def test_meaningful_numbers_kept(self):
        """断言差异里的关键数字必须保留：got 500 与 got 404 是不同根因。"""
        a = "AssertionError: expected status 200 but got 500 for POST /orders"
        b = "AssertionError: expected status 200 but got 404 for POST /orders"
        self.assertEqual(len(cluster_failures([a, b])), 2)


class BoundaryTest(unittest.TestCase):
    def test_all_different(self):
        msgs = [
            "KeyError: 'a'\n  File \"/x.py\", line 1, in fa\n    x()",
            "TypeError: cannot add\n  File \"/y.py\", line 2, in fb\n    y()",
            "ValueError: invalid literal\n  File \"/z.py\", line 3, in fc\n    z()",
            "OSError: disk gone\n  File \"/w.py\", line 4, in fd\n    w()",
        ]
        clusters = cluster_failures(msgs)
        self.assertEqual(len(clusters), 4)
        self.assertTrue(all(c.size == 1 for c in clusters))

    def test_all_same(self):
        template = "KeyError: 'price' (request_id=req-%d, took %dms)"
        msgs = [template % (10000 + i, i * 7 + 1) for i in range(500)]
        clusters = cluster_failures(msgs)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].size, 500)

    def test_single_failure(self):
        clusters = cluster_failures(["RuntimeError: boom"])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].size, 1)
        self.assertEqual(clusters[0].representative, "RuntimeError: boom")

    def test_empty_input(self):
        self.assertEqual(cluster_failures([]), [])

    def test_missing_stack(self):
        """无堆栈时按错误类型+报文聚类，且同类噪声可并、异类可分。"""
        a = "ConnectionRefusedError: redis down (took 31ms, id=777001)"
        b = "ConnectionRefusedError: redis down (took 90012ms, id=777002)"
        c = "PermissionError: /root/.ssh denied"
        clusters = cluster_failures([a, b, c])
        self.assertEqual(len(clusters), 2)
        feats = extract_features(a)
        self.assertEqual(feats['frames'], [])
        self.assertEqual(feats['error_type'], 'ConnectionRefusedError')

    def test_stack_line_numbers_ignored(self):
        a = 'KeyError: \'k\'\n  File "/app/s.py", line 10, in run\n    f()'
        b = 'KeyError: \'k\'\n  File "/app/s.py", line 99, in run\n    f()'
        self.assertEqual(len(cluster_failures([a, b])), 1)

    def test_jvm_frames(self):
        a = ("java.lang.NullPointerException: user is null\n"
             "\tat com.shop.UserService.get(UserService.java:142)\n"
             "\tat com.shop.Ctrl.handle(Ctrl.java:58)")
        b = ("java.lang.NullPointerException: user is null\n"
             "\tat com.shop.UserService.get(UserService.java:977)\n"
             "\tat com.shop.Ctrl.handle(Ctrl.java:12)")
        self.assertEqual(len(cluster_failures([a, b])), 1)


class LabeledEvalTest(unittest.TestCase):
    """与人工标注对拍：F1、漏并率必须达标。"""

    def test_against_labeled_data(self):
        path = os.path.join(BASE, 'labeled_failures.json')
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        messages = [d['message'] for d in data]
        labels = [d['label'] for d in data]
        metrics = evaluate(cluster_failures(messages), labels)
        self.assertGreaterEqual(metrics['pairwise_f1'], 0.99, metrics)
        self.assertLessEqual(metrics['漏并率'], 0.01, metrics)
        self.assertLessEqual(metrics['错并率'], 0.01, metrics)


if __name__ == '__main__':
    unittest.main(verbosity=2)
