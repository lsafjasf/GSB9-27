"""http_ranges 的单元测试、对拍测试与边界计算测试。运行：python3 test_http_ranges.py"""

import random
import unittest

from http_ranges import (
    ByteRange,
    Unsatisfiable,
    build_multipart,
    limit_parts,
    merge_ranges,
    multipart_content_length,
    parse_multipart,
    parse_range_header,
)

BOUNDARY = b"testboundary123"
CT = "application/octet-stream"


def make_data(size: int, seed: int = 42) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(size))


def reader(data: bytes):
    return lambda start, length: data[start : start + length]


class TestParse(unittest.TestCase):
    def test_no_header(self):
        self.assertIsNone(parse_range_header(None, 100))

    def test_other_unit_ignored(self):
        self.assertIsNone(parse_range_header("items=0-10", 100))

    def test_start_to_end(self):
        self.assertEqual(parse_range_header("bytes=10-20", 100), [ByteRange(10, 20)])

    def test_start_to_eof(self):
        self.assertEqual(parse_range_header("bytes=10-", 100), [ByteRange(10, 99)])

    def test_suffix(self):
        self.assertEqual(parse_range_header("bytes=-20", 100), [ByteRange(80, 99)])

    def test_suffix_larger_than_file_clamped(self):
        self.assertEqual(parse_range_header("bytes=-999", 100), [ByteRange(0, 99)])

    def test_end_beyond_eof_clamped(self):
        self.assertEqual(parse_range_header("bytes=90-9999", 100), [ByteRange(90, 99)])

    def test_start_beyond_eof_dropped(self):
        with self.assertRaises(Unsatisfiable):
            parse_range_header("bytes=100-", 100)

    def test_zero_length_range_ignored(self):
        # 5-4 是零长度/反向区间，非法 -> 忽略；剩下 0-9 有效
        self.assertEqual(parse_range_header("bytes=5-4, 0-9", 100), [ByteRange(0, 9)])

    def test_zero_suffix_ignored(self):
        self.assertEqual(parse_range_header("bytes=-0, 3-5", 100), [ByteRange(3, 5)])

    def test_all_invalid_ignored_header(self):
        with self.assertRaises(Unsatisfiable):
            parse_range_header("bytes=5-4, abc, -0", 100)

    def test_empty_file_unsatisfiable(self):
        with self.assertRaises(Unsatisfiable):
            parse_range_header("bytes=0-0", 0)
        with self.assertRaises(Unsatisfiable):
            parse_range_header("bytes=-1", 0)

    def test_full_file_range(self):
        self.assertEqual(parse_range_header("bytes=0-99", 100), [ByteRange(0, 99)])
        self.assertEqual(parse_range_header("bytes=0-", 100), [ByteRange(0, 99)])

    def test_multiple_ranges(self):
        self.assertEqual(
            parse_range_header("bytes=0-9, 31-39, 22-29", 100),
            [ByteRange(0, 9), ByteRange(22, 29), ByteRange(31, 39)],
        )

    def test_whitespace_tolerated(self):
        self.assertEqual(
            parse_range_header("bytes = 1-2 ,  5-6", 100),
            [ByteRange(1, 2), ByteRange(5, 6)],
        )


class TestMergeAndLimit(unittest.TestCase):
    def test_overlap_merged(self):
        self.assertEqual(
            merge_ranges([ByteRange(0, 10), ByteRange(5, 20), ByteRange(15, 30)]),
            [ByteRange(0, 30)],
        )

    def test_adjacent_merged(self):
        self.assertEqual(
            merge_ranges([ByteRange(0, 9), ByteRange(10, 19)]), [ByteRange(0, 19)]
        )

    def test_disjoint_kept(self):
        self.assertEqual(
            merge_ranges([ByteRange(0, 9), ByteRange(11, 19)]),
            [ByteRange(0, 9), ByteRange(11, 19)],
        )

    def test_duplicate_merged(self):
        self.assertEqual(
            merge_ranges([ByteRange(3, 8), ByteRange(3, 8)]), [ByteRange(3, 8)]
        )

    def test_limit_parts_merges_smallest_gap(self):
        parts = [ByteRange(0, 9), ByteRange(11, 19), ByteRange(100, 199)]
        # 间隔 1 的一对先合并
        self.assertEqual(limit_parts(parts, 2), [ByteRange(0, 19), ByteRange(100, 199)])
        self.assertEqual(limit_parts(parts, 1), [ByteRange(0, 199)])

    def test_header_level_limit(self):
        header = ", ".join(f"{i * 10}-{i * 10 + 1}" for i in range(2000))
        parts = parse_range_header("bytes=" + header, 10**9, max_parts=100)
        self.assertEqual(len(parts), 100)


class TestMultipart(unittest.TestCase):
    def test_content_length_matches_build(self):
        data = make_data(5000)
        for header in ["bytes=0-99", "bytes=0-9, 100-199, 4000-", "bytes=-50"]:
            parts = parse_range_header(header, len(data))
            body = build_multipart(parts, reader(data), len(data), CT, BOUNDARY)
            n = multipart_content_length(parts, len(data), CT, BOUNDARY)
            self.assertEqual(len(body), n, header)

    def test_roundtrip_single(self):
        data = make_data(1000)
        parts = parse_range_header("bytes=100-299", len(data))
        body = build_multipart(parts, reader(data), len(data), CT, BOUNDARY)
        parsed = parse_multipart(body, BOUNDARY)
        self.assertEqual(len(parsed), 1)
        s, e, chunk = parsed[0]
        self.assertEqual((s, e), (100, 299))
        self.assertEqual(chunk, data[100:300])

    def test_roundtrip_multi(self):
        data = make_data(1000)
        parts = parse_range_header("bytes=0-9, 500-599, 990-", len(data))
        body = build_multipart(parts, reader(data), len(data), CT, BOUNDARY)
        parsed = parse_multipart(body, BOUNDARY)
        self.assertEqual([(s, e) for s, e, _ in parsed], [(0, 9), (500, 599), (990, 999)])
        for s, e, chunk in parsed:
            self.assertEqual(chunk, data[s : e + 1])


class TestDifferential(unittest.TestCase):
    """对拍：多段响应还原后必须与全量数据按区间切片逐字节一致。"""

    def check(self, data: bytes, header: str):
        parts = parse_range_header(header, len(data))
        body = build_multipart(parts, reader(data), len(data), CT, BOUNDARY)
        self.assertEqual(
            len(body), multipart_content_length(parts, len(data), CT, BOUNDARY)
        )
        parsed = parse_multipart(body, BOUNDARY)
        self.assertEqual(len(parsed), len(parts))
        for (s, e, chunk), p in zip(parsed, parts):
            self.assertEqual((s, e), (p.start, p.end))
            self.assertEqual(chunk, data[p.start : p.end + 1])  # 与全量响应切片对拍
        # 还原缓冲区：按偏移写回，必须与全量数据一致
        buf = bytearray(len(data))
        for s, e, chunk in parsed:
            buf[s : e + 1] = chunk
        for p in parts:
            self.assertEqual(bytes(buf[p.start : p.end + 1]), data[p.start : p.end + 1])

    def test_edge_cases(self):
        data = make_data(1000)
        self.check(data, "bytes=0-999")          # 整文件
        self.check(data, "bytes=0-")             # 整文件（开放结尾）
        self.check(data, "bytes=-1000")          # 后缀=整文件
        self.check(data, "bytes=-100000")        # 后缀越界夹取为整文件
        self.check(data, "bytes=999-999")        # 单字节
        self.check(data, "bytes=0-0")            # 首字节
        self.check(data, "bytes=998-999999")     # end 越界夹取
        self.check(data, "bytes=0-9, 5-15, 14-20")   # 链式重叠合并
        self.check(data, "bytes=0-9, 10-19, 20-29")  # 相邻合并
        self.check(data, "bytes=500-599, 500-599")   # 完全重复
        self.check(data, "bytes=900-950, 0-49, 400-449, 200-250")  # 乱序

    def test_random_fuzz(self):
        rng = random.Random(2026)
        for trial in range(200):
            size = rng.choice([1, 2, 10, 100, 1000, 10000])
            data = make_data(size, seed=trial)
            specs = []
            for _ in range(rng.randrange(1, 30)):
                kind = rng.randrange(3)
                if kind == 0:
                    a = rng.randrange(0, size + 5)
                    b = a + rng.randrange(0, size // 2 + 2)
                    specs.append(f"{a}-{b}")
                elif kind == 1:
                    specs.append(f"{rng.randrange(0, size)}-")
                else:
                    specs.append(f"-{rng.randrange(1, size + 5)}")
            header = "bytes=" + ", ".join(specs)
            try:
                self.check(data, header)
            except Unsatisfiable:
                # 全部区间 start >= size 才可能出现；此时应返回 416，无多段体
                pass

    def test_thousands_of_ranges(self):
        size = 2_000_000
        data = make_data(size, seed=7)
        rng = random.Random(1)
        specs = []
        for _ in range(2000):
            a = rng.randrange(0, size - 100)
            specs.append(f"{a}-{a + rng.randrange(0, 100)}")
        self.check(data, "bytes=" + ", ".join(specs))

    def test_empty_file(self):
        self.assertIsNone(parse_range_header(None, 0))  # 无头 -> 全量（空体）
        with self.assertRaises(Unsatisfiable):
            parse_range_header("bytes=0-", 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
