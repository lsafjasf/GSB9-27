"""paged_index 自测：空索引、单页、跨页、不整除、截断、页头/负载损坏、越界防护。"""

import os
import random
import struct
import tempfile
import unittest

import paged_index as pi


def make_records(n, record_size=16, seed=0, sparse=False):
    rng = random.Random(seed)
    keys = set()
    while len(keys) < n:
        keys.add(rng.randrange(0, 1 << 63) if sparse else rng.randrange(n * 4))
    recs = []
    for k in sorted(keys):
        payload_len = min(8, record_size - pi.KEY_SIZE)
        recs.append((k, struct.pack(">Q", k)[:payload_len]))
    return recs


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "idx.bin")

    def tearDown(self):
        self.dir.cleanup()

    def build(self, records, page_size=4096, record_size=16):
        return pi.build_index(self.path, records, page_size, record_size)


class TestLayout(Base):
    def test_empty_index(self):
        st = self.build([], page_size=512, record_size=16)
        self.assertEqual(st["num_pages"], 1)
        self.assertEqual(os.path.getsize(self.path), 512)
        with pi.PagedIndexReader(self.path) as r:
            self.assertEqual(r.num_records, 0)
            self.assertIsNone(r.lookup(0))
            self.assertIsNone(r.lookup(1 << 60))
            with self.assertRaises(IndexError):
                r.record_at(0)

    def test_single_page(self):
        recs = make_records(50, seed=1)
        st = self.build(recs, page_size=4096, record_size=16)
        self.assertEqual(st["num_pages"], 2)  # 头页 + 1 数据页
        with pi.PagedIndexReader(self.path) as r:
            for k, v in recs:
                got = r.lookup(k)
                self.assertIsNotNone(got)
                self.assertEqual(got[:len(v)], v)
            self.assertIsNone(r.lookup((1 << 63) + 7))

    def test_cross_page(self):
        # 255 条/页，1000 条必然跨 4 页
        recs = make_records(1000, seed=2)
        st = self.build(recs, page_size=4096, record_size=16)
        self.assertEqual(st["num_pages"], 5)
        with pi.PagedIndexReader(self.path) as r:
            for k, v in recs:
                self.assertEqual(r.lookup(k)[:len(v)], v)
            # 页边界上的 key（每页首/尾记录）
            for i in (0, 254, 255, 256, 509, 510, 999):
                k, v = recs[i]
                self.assertEqual(r.lookup(k)[:len(v)], v)
            # 不存在的 key：介于已有 key 之间
            self.assertIsNone(r.lookup(recs[0][0] + 1)
                              if recs[0][0] + 1 != recs[1][0] else None)

    def test_page_size_not_divisible_by_record_size(self):
        # (512 - 16) % 24 = 16 != 0，页尾有 16 字节填充
        record_size, page_size = 24, 512
        rpp = pi.records_per_page(page_size, record_size)
        self.assertEqual(rpp, 20)
        self.assertNotEqual((page_size - pi.PAGE_HEADER_SIZE) % record_size, 0)
        recs = [(i * 3, struct.pack(">Q", i * 3)) for i in range(205)]
        st = self.build(recs, page_size=page_size, record_size=record_size)
        self.assertEqual(st["num_pages"], 1 + (205 + 19) // 20)
        self.assertEqual(os.path.getsize(self.path), st["num_pages"] * page_size)
        with pi.PagedIndexReader(self.path) as r:
            self.assertEqual(r.records_per_page, 20)
            for k, v in recs:
                self.assertEqual(r.lookup(k)[:8], v)
            for i in (19, 20, 21, 199, 200, 204):  # 跨页边界
                self.assertEqual(r.record_at(i)[0], recs[i][0])

    def test_unsorted_input_and_binary_search_order(self):
        recs = make_records(300, seed=3)
        shuffled = recs[:]
        random.Random(9).shuffle(shuffled)
        self.build(shuffled)
        with pi.PagedIndexReader(self.path) as r:
            keys = [r.record_at(i)[0] for i in range(r.num_records)]
            self.assertEqual(keys, sorted(keys))

    def test_fuzz_against_dict(self):
        recs = make_records(2000, record_size=32, seed=4, sparse=True)
        ref = {k: v for k, v in recs}
        self.build(recs, record_size=32)
        rng = random.Random(5)
        with pi.PagedIndexReader(self.path) as r:
            for _ in range(5000):
                k = rng.randrange(0, 1 << 63)
                got = r.lookup(k)
                if k in ref:
                    self.assertIsNotNone(got)
                    self.assertEqual(got[:len(ref[k])], ref[k])
                else:
                    self.assertIsNone(got)


class TestCorruption(Base):
    def setUp(self):
        super().setUp()
        self.recs = make_records(600, seed=6)
        self.st = self.build(self.recs, page_size=4096, record_size=16)

    def _poke(self, offset, data):
        with open(self.path, "r+b") as f:
            f.seek(offset)
            f.write(data)

    def test_truncated_file_detected_at_open(self):
        size = os.path.getsize(self.path)
        with open(self.path, "r+b") as f:
            f.truncate(size - 1000)  # 截掉最后一页的一部分
        with self.assertRaises(pi.TruncatedError) as cm:
            pi.PagedIndexReader(self.path)
        err = cm.exception
        self.assertEqual(err.expected, size)
        self.assertEqual(err.actual, size - 1000)
        self.assertIn("page", str(err))  # 错误信息可定位到页

    def test_truncated_to_partial_header(self):
        with open(self.path, "r+b") as f:
            f.truncate(20)
        with self.assertRaises(pi.TruncatedError):
            pi.PagedIndexReader(self.path)

    def test_bad_file_magic(self):
        self._poke(0, b"XXXXXXX1")
        with self.assertRaises(pi.FormatError):
            pi.PagedIndexReader(self.path)

    def test_bad_header_crc(self):
        # 修改 num_records 字段（offset 32）但不更新 CRC
        self._poke(32, struct.pack(">Q", 12345))
        with self.assertRaises(pi.FormatError):
            pi.PagedIndexReader(self.path)

    def test_bad_page_magic(self):
        # 页 2 起始偏移 = 2 * 4096
        self._poke(2 * 4096, b"\xde\xad\xbe\xef")
        with pi.PagedIndexReader(self.path) as r:
            with self.assertRaises(pi.CorruptPageError) as cm:
                r.record_at(300)  # 记录 300 在页 2
            self.assertEqual(cm.exception.page_no, 2)
            self.assertEqual(cm.exception.offset, 2 * 4096)

    def test_page_number_mismatch(self):
        self._poke(4096 + 4, struct.pack(">I", 99))  # 页 1 的 page_no 字段
        with pi.PagedIndexReader(self.path) as r:
            with self.assertRaises(pi.CorruptPageError) as cm:
                r.record_at(0)
            self.assertIn("mismatch", cm.exception.reason)

    def test_payload_corruption_detected_by_crc(self):
        # 翻转页 1 第一条记录的 key 字节（页头后 offset 16）
        with open(self.path, "rb") as f:
            f.seek(4096 + 16)
            orig = f.read(1)
        self._poke(4096 + 16, bytes([orig[0] ^ 0xFF]))
        with pi.PagedIndexReader(self.path) as r:
            with self.assertRaises(pi.CorruptPageError) as cm:
                r.record_at(0)
            self.assertIn("CRC", cm.exception.reason)

    def test_truncation_after_open_detected_on_read(self):
        with pi.PagedIndexReader(self.path) as r:
            size = os.path.getsize(self.path)
            with open(self.path, "r+b") as f:
                f.truncate(size - 100)  # 打开后再截断
            r._cache.clear()
            with self.assertRaises(pi.TruncatedError):
                r.record_at(self.st["num_records"] - 1)


class TestNoOutOfBounds(Base):
    def test_reads_stay_within_file(self):
        recs = make_records(1000, seed=7)
        self.build(recs)
        size = os.path.getsize(self.path)
        calls = []
        real_pread = os.pread

        def spy(fd, n, off):
            calls.append((off, n))
            return real_pread(fd, n, off)

        rng = random.Random(8)
        with pi.PagedIndexReader(self.path) as r:
            try:
                pi.os.pread = spy
                for _ in range(2000):
                    r.lookup(rng.randrange(0, 4000))
            finally:
                pi.os.pread = real_pread
        self.assertTrue(calls)
        for off, n in calls:
            self.assertGreaterEqual(off, 0)
            self.assertLessEqual(off + n, size)
            self.assertEqual(off % r.page_size, 0)  # 页对齐
            self.assertEqual(n, r.page_size)        # 整页读取

    def test_bounded_memory_cache(self):
        recs = make_records(3000, seed=9)
        self.build(recs)
        with pi.PagedIndexReader(self.path, cache_pages=8) as r:
            rng = random.Random(10)
            for _ in range(5000):
                r.lookup(rng.randrange(0, 12000))
            self.assertLessEqual(len(r._cache), 8)
            # 每次查询只读 O(log n) 页
            self.assertLess(r.pages_read, 5000 * 14)


if __name__ == "__main__":
    unittest.main(verbosity=2)
