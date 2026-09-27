"""failcluster 自测：噪声抑制、边界输入、特征抽取、聚类正确性。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from failcluster import (  # noqa: E402
    FailureRecord,
    cluster_failures,
    extract_features,
    normalize,
    signature_of,
)


def rec(i, text):
    return FailureRecord(id=f"T-{i}", text=text)


class TestNormalize(unittest.TestCase):
    """噪声抑制：随机序列号、内存地址、耗时数字不得影响归一化结果。"""

    def test_memory_address(self):
        a = normalize("crash at 0x7f8b4c00a1b0 object 0x10ff23")
        b = normalize("crash at 0xdeadbeef object 0x1")
        self.assertEqual(a, b)
        self.assertNotIn("0x", a)

    def test_serial_number(self):
        a = normalize("request ORD-1234567890 failed, seq 99887766")
        b = normalize("request ORD-5550001112 failed, seq 11223344")
        self.assertEqual(a, b)

    def test_duration(self):
        a = normalize("timed out after 3210ms, took 1.25s, elapsed=9s")
        b = normalize("timed out after 87ms, took 0.03s, elapsed=1s")
        self.assertEqual(a, b)
        self.assertNotIn("3210", a)

    def test_timestamp_and_uuid(self):
        a = normalize("at 2026-09-28T03:11:45Z id 3f6b2a10-1234-4abc-9def-001122334455")
        b = normalize("at 2025-01-01T00:00:00Z id 00000000-0000-0000-0000-000000000000")
        self.assertEqual(a, b)

    def test_small_numbers_preserved(self):
        # 小整数（如断言值、错误码）不应被吞掉
        self.assertIn("3", normalize("expected 3 got 5"))
        self.assertIn("404", normalize("status 404"))

    def test_noise_does_not_change_signature(self):
        t1 = (
            "Traceback (most recent call last):\n"
            '  File "a.py", line 10, in f\n'
            "ValueError: bad id 123456 at 0xaaaabbbb after 12ms"
        )
        t2 = (
            "Traceback (most recent call last):\n"
            '  File "a.py", line 99, in f\n'
            "ValueError: bad id 987654 at 0x1234 after 9999ms"
        )
        self.assertEqual(signature_of(extract_features(t1)),
                         signature_of(extract_features(t2)))


class TestExtractFeatures(unittest.TestCase):
    def test_error_type_and_frames(self):
        text = (
            "Traceback (most recent call last):\n"
            '  File "app/x.py", line 1, in outer\n'
            '  File "app/y.py", line 2, in inner\n'
            "KeyError: 'k'"
        )
        f = extract_features(text)
        self.assertEqual(f.error_type, "KeyError")
        self.assertIn("y.py:inner", f.frames)

    def test_line_numbers_ignored(self):
        t1 = 'File "a.py", line 1, in f\nIOError: x'
        t2 = 'File "a.py", line 999, in f\nIOError: x'
        self.assertEqual(extract_features(t1).frames, extract_features(t2).frames)

    def test_assertion_diff(self):
        f = extract_features("AssertionError: 'USD' != 'CNY'")
        self.assertIsNotNone(f.assertion)
        self.assertIn("USD", f.assertion[0])
        self.assertIn("CNY", f.assertion[1])

    def test_assertion_noise_normalized(self):
        f1 = extract_features("AssertionError: 12345678 != 87654321")
        f2 = extract_features("AssertionError: 11112222 != 33334444")
        self.assertEqual(f1.assertion, f2.assertion)

    def test_missing_stack(self):
        f = extract_features("RuntimeError: worker crashed, seq 12345678")
        self.assertEqual(f.frames, ())
        self.assertEqual(f.error_type, "RuntimeError")

    def test_no_error_line_at_all(self):
        f = extract_features("lint failure: E501 line too long in report #12345")
        self.assertEqual(f.error_type, "UnknownError")
        self.assertTrue(f.message)


class TestClustering(unittest.TestCase):
    def test_all_identical(self):
        recs = [rec(i, "ValueError: boom") for i in range(50)]
        clusters = cluster_failures(recs)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].size, 50)

    def test_all_distinct(self):
        recs = [rec(i, f"ValueError: distinct small code {i}") for i in range(30)]
        clusters = cluster_failures(recs)
        self.assertEqual(len(clusters), 30)
        self.assertTrue(all(c.size == 1 for c in clusters))

    def test_single_record(self):
        clusters = cluster_failures([rec(1, "KeyError: 'a'")])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].size, 1)
        self.assertEqual(clusters[0].representative.id, "T-1")

    def test_empty_input(self):
        self.assertEqual(cluster_failures([]), [])

    def test_missing_stack_groups_by_type_and_message(self):
        recs = [
            rec(1, "RuntimeError: worker crashed, dump at 0xaaaa1111, seq 10000001"),
            rec(2, "RuntimeError: worker crashed, dump at 0xbbbb2222, seq 20000002"),
            rec(3, "RuntimeError: disk full, seq 10000001"),
        ]
        clusters = cluster_failures(recs)
        self.assertEqual(len(clusters), 2)
        sizes = sorted(c.size for c in clusters)
        self.assertEqual(sizes, [1, 2])

    def test_noise_variants_merge(self):
        base = (
            "Traceback (most recent call last):\n"
            '  File "app/db.py", line {line}, in acquire\n'
            "TimeoutError: pool timeout after {ms}ms (req {serial}, addr {addr})"
        )
        recs = [
            rec(1, base.format(line=10, ms=100, serial=11111111, addr="0xaaa")),
            rec(2, base.format(line=87, ms=9999, serial=22222222, addr="0xbbb")),
            rec(3, base.format(line=300, ms=5, serial=33333333, addr="0xccc")),
        ]
        clusters = cluster_failures(recs)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].size, 3)

    def test_similar_but_different_root_cause_not_merged(self):
        t1 = 'File "a.py", line 1, in f\nTimeoutError: db timed out'
        t2 = 'File "a.py", line 1, in f\nConnectionRefusedError: db refused'
        clusters = cluster_failures([rec(1, t1), rec(2, t2)])
        self.assertEqual(len(clusters), 2)

    def test_representative_is_member(self):
        recs = [rec(i, f"ValueError: v{i % 2}") for i in range(10)]
        clusters = cluster_failures(recs)
        for c in clusters:
            self.assertIn(c.representative, c.members)

    def test_sorted_by_size_desc(self):
        recs = [rec(i, "ValueError: a") for i in range(5)]
        recs += [rec(100 + i, "KeyError: b") for i in range(20)]
        clusters = cluster_failures(recs)
        self.assertEqual(clusters[0].size, 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
