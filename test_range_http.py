"""rangelib 的单元测试 + 与全量响应的逐字节对拍。

运行：python3 -m unittest test_range_http -v   （或 python3 test_range_http.py）
"""

import random
import sys
import unittest

from rangelib.core import (
    DEFAULT_BOUNDARY,
    RangeNotSatisfiable,
    build_multipart_body,
    merge_ranges,
    multipart_content_length,
    parse_multipart_body,
    parse_range_header,
)

CT = "application/octet-stream"


def roundtrip(data: bytes, header: str, max_parts: int = 64):
    """解析 Range 头 -> 构造多段响应 -> 解析还原 -> 与全量数据逐字节对拍。"""
    size = len(data)
    ranges = parse_range_header(header, size, max_parts=max_parts)
    body = build_multipart_body(ranges, data, CT)
    # 边界与长度计算：预算长度必须等于实际长度
    assert multipart_content_length(ranges, size, CT) == len(body)
    parts = parse_multipart_body(body, DEFAULT_BOUNDARY)
    assert len(parts) == len(ranges)
    for (start, end, total), payload in parts:
        assert total == size
        assert (start, end) in ranges
        # 与全量响应拼装对拍：逐字节一致
        assert payload == data[start:end + 1], (start, end)
    return ranges, parts


class TestParse(unittest.TestCase):
    def test_start_to_end(self):
        self.assertEqual(parse_range_header("bytes=0-99", 1000), [(0, 99)])

    def test_start_to_eof(self):
        self.assertEqual(parse_range_header("bytes=100-", 1000), [(100, 999)])

    def test_suffix(self):
        self.assertEqual(parse_range_header("bytes=-50", 1000), [(950, 999)])

    def test_suffix_larger_than_file_clamps_to_whole(self):
        self.assertEqual(parse_range_header("bytes=-99999", 1000), [(0, 999)])

    def test_end_beyond_eof_clamped(self):
        self.assertEqual(parse_range_header("bytes=900-99999", 1000), [(900, 999)])

    def test_start_beyond_eof_ignored(self):
        self.assertEqual(parse_range_header("bytes=0-9, 5000-6000", 1000), [(0, 9)])

    def test_all_out_of_bounds_raises_416(self):
        with self.assertRaises(RangeNotSatisfiable):
            parse_range_header("bytes=1000-2000", 1000)

    def test_empty_file_raises_416(self):
        with self.assertRaises(RangeNotSatisfiable):
            parse_range_header("bytes=0-0", 0)

    def test_zero_length_range_ignored(self):
        # start > end 是零长度/反向区间：忽略该条，保留其余
        self.assertEqual(parse_range_header("bytes=5-4, 10-20", 1000), [(10, 20)])

    def test_zero_length_suffix_ignored(self):
        self.assertEqual(parse_range_header("bytes=-0, 0-9", 1000), [(0, 9)])

    def test_all_zero_length_raises_416(self):
        with self.assertRaises(RangeNotSatisfiable):
            parse_range_header("bytes=5-4, -0", 1000)

    def test_garbage_specs_ignored(self):
        self.assertEqual(parse_range_header("bytes=abc, 0-9, 1-2-3, -", 1000),
                         [(0, 9)])

    def test_case_and_whitespace(self):
        # unit 大小写不敏感、逗号后允许空白；不相邻区间不合并
        self.assertEqual(parse_range_header("Bytes=0-9, 20-29", 1000),
                         [(0, 9), (20, 29)])

    def test_bad_unit_rejected(self):
        with self.assertRaises(ValueError):
            parse_range_header("items=0-9", 1000)

    def test_whole_file(self):
        self.assertEqual(parse_range_header("bytes=0-", 1000), [(0, 999)])
        self.assertEqual(parse_range_header("bytes=0-999", 1000), [(0, 999)])

    def test_single_byte(self):
        self.assertEqual(parse_range_header("bytes=0-0", 1), [(0, 0)])


class TestMerge(unittest.TestCase):
    def test_overlap_merged(self):
        self.assertEqual(parse_range_header("bytes=0-100, 50-200", 1000),
                         [(0, 200)])

    def test_adjacent_merged(self):
        self.assertEqual(parse_range_header("bytes=0-99, 100-199", 1000),
                         [(0, 199)])

    def test_contained_merged(self):
        self.assertEqual(parse_range_header("bytes=0-500, 100-200", 1000),
                         [(0, 500)])

    def test_disjoint_kept_sorted(self):
        self.assertEqual(parse_range_header("bytes=500-599, 0-99", 1000),
                         [(0, 99), (500, 599)])

    def test_max_parts_merges_smallest_gap(self):
        # 5 段不相邻区间，限制 2 段 -> 间隙最小的先合并
        ranges = [(0, 9), (12, 19), (30, 39), (100, 109), (200, 209)]
        merged = merge_ranges(ranges, max_parts=2)
        self.assertEqual(len(merged), 2)
        # 间隙依次为 2/10/60/90，逐步合并最小间隙 -> (0,109) 与 (200,209)
        self.assertEqual(merged, [(0, 109), (200, 209)])


class TestMultipart(unittest.TestCase):
    def test_content_length_matches_built_body(self):
        data = bytes(random.Random(1).randbytes(5000))
        ranges = parse_range_header("bytes=0-99, 200-299, 4000-", len(data))
        body = build_multipart_body(ranges, data, CT)
        self.assertEqual(multipart_content_length(ranges, len(data), CT),
                         len(body))

    def test_single_range_roundtrip(self):
        data = b"hello world"
        ranges, parts = roundtrip(data, "bytes=0-4")
        self.assertEqual(parts[0][1], b"hello")

    def test_empty_file_unsatisfiable(self):
        with self.assertRaises(RangeNotSatisfiable):
            parse_range_header("bytes=0-", 0)

    def test_whole_file_roundtrip(self):
        data = random.Random(2).randbytes(10000)
        ranges, parts = roundtrip(data, "bytes=0-")
        self.assertEqual(ranges, [(0, len(data) - 1)])
        self.assertEqual(parts[0][1], data)

    def test_out_of_bounds_range_built_raises(self):
        with self.assertRaises(ValueError):
            build_multipart_body([(0, 100)], b"short", CT)


class TestFuzzRoundtrip(unittest.TestCase):
    """随机文件 + 随机区间（含重叠、越界、零长度、乱序），对拍 500 轮。"""

    def test_fuzz(self):
        rng = random.Random(20260928)
        for trial in range(500):
            size = rng.choice([1, 2, 3, 10, 100, 1000, 65536])
            data = rng.randbytes(size)
            specs = []
            for _ in range(rng.randint(1, 20)):
                kind = rng.randint(0, 3)
                if kind == 0:
                    a = rng.randint(0, size + 50)
                    b = rng.randint(0, size + 50)
                    specs.append("%d-%d" % (a, b))
                elif kind == 1:
                    specs.append("%d-" % rng.randint(0, size + 50))
                elif kind == 2:
                    specs.append("-%d" % rng.randint(0, size + 50))
                else:
                    specs.append(rng.choice(["abc", "-", "1-2-3", ""]))
            header = "bytes=" + ",".join(specs)
            try:
                ranges, parts = roundtrip(data, header,
                                          max_parts=rng.choice([1, 4, 64]))
            except RangeNotSatisfiable:
                continue
            # 还原拼装必须与全量数据逐字节一致
            reassembled = b"".join(p for _, p in parts)
            expected = b"".join(data[s:e + 1] for s, e in ranges)
            self.assertEqual(reassembled, expected)
            # 区间必须有序、不重叠、在界内
            for i, (s, e) in enumerate(ranges):
                self.assertTrue(0 <= s <= e < size)
                if i:
                    self.assertGreater(s, ranges[i - 1][1])

    def test_thousands_of_ranges(self):
        rng = random.Random(7)
        size = 2_000_000
        data = rng.randbytes(size)
        specs = []
        for _ in range(2000):
            a = rng.randint(0, size - 1)
            b = min(size - 1, a + rng.randint(0, 5000))
            specs.append("%d-%d" % (a, b))
        ranges, parts = roundtrip(data, "bytes=" + ",".join(specs),
                                  max_parts=10_000)
        self.assertGreater(len(ranges), 100)  # 大量重叠后仍有很多段
        reassembled = b"".join(p for _, p in parts)
        expected = b"".join(data[s:e + 1] for s, e in ranges)
        self.assertEqual(reassembled, expected)

    def test_thousands_capped_by_max_parts(self):
        rng = random.Random(8)
        size = 1_000_000
        specs = []
        for i in range(3000):
            a = rng.randint(0, size - 100)
            specs.append("%d-%d" % (a, a + 99))
        ranges = parse_range_header("bytes=" + ",".join(specs), size,
                                    max_parts=64)
        self.assertLessEqual(len(ranges, ), 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
