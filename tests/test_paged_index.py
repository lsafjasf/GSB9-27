import os
import random
import struct
import tempfile
import unittest

from paged_index import (
    CorruptHeaderError,
    CorruptPageError,
    PagedIndexReader,
    TruncatedIndexError,
    build_index,
)
from paged_index import format as fmt


class TempIndexCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "test.idx")

    def tearDown(self):
        self.dir.cleanup()

    def build(self, records, page_size=4096, record_size=16):
        return build_index(
            self.path, records, page_size=page_size, record_size=record_size
        )

    def corrupt_at(self, offset, data):
        with open(self.path, "r+b") as fh:
            fh.seek(offset)
            fh.write(data)


class TestEmptyIndex(TempIndexCase):
    def test_empty(self):
        n = self.build([])
        self.assertEqual(n, 0)
        self.assertEqual(os.path.getsize(self.path), 4096)  # header page only
        with PagedIndexReader(self.path) as reader:
            self.assertEqual(len(reader), 0)
            self.assertIsNone(reader.lookup(123))
            with self.assertRaises(IndexError):
                reader.record_at(0)


class TestSinglePage(TempIndexCase):
    def test_single_page(self):
        records = [(i * 3, i) for i in range(50)]
        self.build(records)
        self.assertEqual(os.path.getsize(self.path), 2 * 4096)  # hdr + 1 page
        with PagedIndexReader(self.path) as reader:
            self.assertEqual(len(reader), 50)
            for key, value in records:
                got = reader.lookup(key)
                self.assertIsNotNone(got)
                self.assertEqual(int.from_bytes(got, "little"), value)
            self.assertIsNone(reader.lookup(1))  # gap between keys
            self.assertIsNone(reader.lookup(10_000))


class TestCrossPage(TempIndexCase):
    def test_cross_page_binary_search(self):
        # 4096-byte page, 16-byte records -> 254 records/page; use 1000
        # records so the index spans 4 data pages.
        cap = fmt.capacity(4096, 16)
        self.assertEqual(cap, 254)
        total = 1000
        records = [(i * 2, i ^ 0xABCD) for i in range(total)]
        self.build(records)
        self.assertEqual(os.path.getsize(self.path), (1 + 4) * 4096)
        with PagedIndexReader(self.path, cache_pages=2) as reader:
            self.assertEqual(len(reader), total)
            # boundary records: first/last of every page plus neighbours
            boundary = sorted(
                {0, total - 1}
                | {i * cap + d for i in range(4) for d in (-1, 0, 1)}
                & set(range(total))
            )
            for i in boundary:
                key, value = records[i]
                got = reader.lookup(key)
                self.assertEqual(
                    int.from_bytes(got, "little"), value, f"record {i}"
                )
            # full sweep with random order to force cross-page movement
            order = list(range(total))
            random.Random(7).shuffle(order)
            for i in order:
                got = reader.lookup(records[i][0])
                self.assertEqual(int.from_bytes(got, "little"), records[i][1])
            # record_at random access across pages
            for i in (0, cap - 1, cap, 2 * cap, total - 1):
                key, value = reader.record_at(i)
                self.assertEqual((key, int.from_bytes(value, "little")), records[i])

    def test_offset_math(self):
        # documented formula: (page_no + 1) * page_size + 32 + slot * rec
        self.assertEqual(fmt.record_offset(0, 4096, 16), 4096 + 32)
        self.assertEqual(fmt.record_offset(253, 4096, 16), 4096 + 32 + 253 * 16)
        self.assertEqual(fmt.record_offset(254, 4096, 16), 2 * 4096 + 32)


class TestNonDivisibleRecordSize(TempIndexCase):
    def test_padding_when_not_divisible(self):
        # 512-byte pages, 28-byte records: payload 480 -> 17 records,
        # 4 bytes of zero padding per page.
        page_size, record_size = 512, 28
        cap = fmt.capacity(page_size, record_size)
        self.assertEqual(cap, 17)
        self.assertEqual(page_size - fmt.PAGE_HEADER_SIZE - cap * record_size, 4)
        total = cap * 3 + 5  # 3 full pages + partial last page
        records = [(i * 5, i) for i in range(total)]
        self.build(records, page_size=page_size, record_size=record_size)
        self.assertEqual(os.path.getsize(self.path), (1 + 4) * page_size)
        with PagedIndexReader(self.path) as reader:
            self.assertEqual(len(reader), total)
            self.assertEqual(reader.record_size, record_size)
            for key, value in records:
                got = reader.lookup(key)
                self.assertEqual(int.from_bytes(got, "little"), value)
            # padding bytes are zero and covered by the page CRC
            with open(self.path, "rb") as fh:
                fh.seek(page_size)  # first data page
                page = fh.read(page_size)
            pad_start = fmt.PAGE_HEADER_SIZE + cap * record_size
            self.assertEqual(page[pad_start:], b"\x00" * 4)

    def test_invalid_geometry_rejected(self):
        with self.assertRaises(ValueError):
            self.build([], page_size=100, record_size=16)  # not multiple of 64
        with self.assertRaises(ValueError):
            self.build([], page_size=128, record_size=4)  # record too small
        with self.assertRaises(ValueError):
            self.build([], page_size=128, record_size=200)  # no record fits


class TestTruncation(TempIndexCase):
    def test_truncated_file_detected_at_open(self):
        self.build([(i, i) for i in range(600)])  # 3 data pages
        full = os.path.getsize(self.path)
        with open(self.path, "r+b") as fh:
            fh.truncate(full - 100)  # cut inside the last page
        with self.assertRaises(TruncatedIndexError) as ctx:
            PagedIndexReader(self.path)
        self.assertIn("truncated", str(ctx.exception))
        self.assertEqual(ctx.exception.offset, full - 100)

    def test_truncation_after_open_detected_per_page(self):
        self.build([(i, i) for i in range(600)])
        full = os.path.getsize(self.path)
        reader = PagedIndexReader(self.path)
        reader.lookup(0)  # works, touches early pages only
        with open(self.path, "r+b") as fh:
            fh.truncate(full - 10)
        # a query that needs the last page must fail with a locatable error
        with self.assertRaises(TruncatedIndexError) as ctx:
            reader.lookup(599)
        self.assertEqual(ctx.exception.page_no, 2)
        self.assertEqual(ctx.exception.offset, 3 * 4096)
        reader.close()

    def test_tiny_file(self):
        with open(self.path, "wb") as fh:
            fh.write(b"PG")
        with self.assertRaises(TruncatedIndexError):
            PagedIndexReader(self.path)


class TestCorruption(TempIndexCase):
    def build_default(self):
        self.build([(i * 2, i) for i in range(600)])  # 3 data pages

    def test_bad_file_magic(self):
        self.build_default()
        self.corrupt_at(0, b"XXXXXXXX")
        with self.assertRaises(CorruptHeaderError) as ctx:
            PagedIndexReader(self.path)
        self.assertEqual(ctx.exception.offset, 0)

    def test_bad_header_crc(self):
        self.build_default()
        self.corrupt_at(20, struct.pack("<Q", 999))  # num_records field
        with self.assertRaises(CorruptHeaderError) as ctx:
            PagedIndexReader(self.path)
        self.assertIn("checksum", str(ctx.exception))

    def test_bad_page_magic(self):
        self.build_default()
        page_no = 1
        self.corrupt_at((page_no + 1) * 4096, b"BAAD")
        with PagedIndexReader(self.path) as reader:
            with self.assertRaises(CorruptPageError) as ctx:
                reader.record_at(254 * page_no)  # first record of page 1
            self.assertEqual(ctx.exception.page_no, page_no)
            self.assertEqual(ctx.exception.offset, (page_no + 1) * 4096)

    def test_bad_page_number(self):
        self.build_default()
        self.corrupt_at(4096 + 4, struct.pack("<I", 77))  # page_no field
        with PagedIndexReader(self.path) as reader:
            with self.assertRaises(CorruptPageError) as ctx:
                reader.record_at(0)
            self.assertIn("page number mismatch", str(ctx.exception))

    def test_bad_record_count(self):
        self.build_default()
        self.corrupt_at(4096 + 8, struct.pack("<I", 1))
        with PagedIndexReader(self.path) as reader:
            with self.assertRaises(CorruptPageError) as ctx:
                reader.record_at(0)
            self.assertIn("record_count", str(ctx.exception))

    def test_corrupt_payload_crc(self):
        self.build_default()
        # flip one byte inside a record on data page 1
        self.corrupt_at(2 * 4096 + fmt.PAGE_HEADER_SIZE + 3, b"\xff")
        with PagedIndexReader(self.path) as reader:
            with self.assertRaises(CorruptPageError) as ctx:
                reader.record_at(254)
            self.assertIn("checksum mismatch", str(ctx.exception))
            self.assertEqual(ctx.exception.page_no, 1)

    def test_corruption_does_not_traverse_pages(self):
        # an intact page must still be served even if a later page is bad;
        # and the bad page must never yield out-of-bounds data.
        self.build_default()
        self.corrupt_at(3 * 4096, b"JUNK")  # page 2 magic
        with PagedIndexReader(self.path) as reader:
            self.assertIsNotNone(reader.lookup(0))  # page 0 fine
            with self.assertRaises(CorruptPageError):
                reader.lookup(2 * 599)  # key lives on page 2


class TestBuilderValidation(TempIndexCase):
    def test_unsorted_rejected(self):
        with self.assertRaises(ValueError):
            self.build([(5, 1), (3, 2)])

    def test_value_length_checked(self):
        with self.assertRaises(ValueError):
            self.build([(1, b"too short")])


if __name__ == "__main__":
    unittest.main()
