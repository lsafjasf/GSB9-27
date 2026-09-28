"""Self-tests for snapchunk: round-trip, index, corruption, recovery.

Run:  python3 -m unittest -v test_snapchunk   (or  python3 test_snapchunk.py)
"""

import os
import random
import shutil
import struct
import tempfile
import unittest

import snapchunk as sc


def make_snapshot(seed: int, size: int) -> bytes:
    """Mixed-content payload: zeros, repetitive text, random bytes."""
    rng = random.Random(seed)
    zeros = b"\x00" * (size // 3)
    text = (b"the quick brown fox jumps over the lazy dog. " * 64)[: size // 3]
    tail = bytes(rng.randrange(256) for _ in range(size - len(zeros) - len(text)))
    data = zeros + text + tail
    rng.shuffle(bytearray(data))  # note: shuffle on a throwaway copy below
    return zeros + text + tail  # unshuffled; keeps compressibility realistic


def block_payload_offset(ar: sc.Archive, index: int) -> int:
    return ar.entries[index].offset + sc._F_BLOCK.size


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="snapchunk-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def assertRoundTrips(self, data, block_size):
        src, dst = self.path("in.bin"), self.path("out.snpk")
        with open(src, "wb") as fh:
            fh.write(data)
        with open(src, "rb") as fh:
            info = sc.compress_snapshot(fh, dst, block_size=block_size)
        self.assertEqual(info.original_size, len(data))
        self.assertEqual(
            info.num_blocks, sc._num_blocks(len(data), block_size)
        )
        restored = sc.extract_snapshot(dst)
        # Lossless byte-for-byte assertion required by the spec.
        self.assertEqual(restored, data)
        return dst, info

    def test_empty_snapshot(self):
        dst, info = self.assertRoundTrips(b"", 4096)
        self.assertEqual(info.num_blocks, 0)
        self.assertEqual(info.compressed_size,
                         sc.HEADER_SIZE + sc._FOOTER_SIZE)
        with sc.Archive(dst) as ar:
            self.assertEqual(ar.num_blocks, 0)
            self.assertEqual(ar.original_size, 0)
            self.assertEqual(ar.read_range(0, 0), b"")
            self.assertEqual(ar.read_range(0), b"")
            self.assertEqual(ar.extract_all(), b"")
            self.assertTrue(ar.verify().ok)

    def test_single_block_smaller_than_block_size(self):
        data = b"hello snapshot " * 10
        self.assertRoundTrips(data, 1 << 20)

    def test_single_block_exact_multiple(self):
        data = bytes((i * 7) % 256 for i in range(4096))
        self.assertRoundTrips(data, 4096)

    def test_partial_final_block(self):
        data = b"z" * 10000
        dst, _ = self.assertRoundTrips(data, 4096)
        with sc.Archive(dst) as ar:
            self.assertEqual(len(ar.entries), 3)
            self.assertEqual(ar.entries[-1].original_size, 10000 - 8192)

    def test_many_blocks_over_one_thousand(self):
        block_size = 1024
        data = make_snapshot(42, 1300 * block_size + 333)
        dst, info = self.assertRoundTrips(data, block_size)
        self.assertEqual(info.num_blocks, 1301)
        with sc.Archive(dst) as ar:
            # every index entry must point at distinct, in-bounds regions
            offsets = [e.offset for e in ar.entries]
            self.assertEqual(offsets, sorted(set(offsets)))
            for e in ar.entries:
                self.assertGreaterEqual(e.offset, sc.HEADER_SIZE)
                self.assertLess(
                    e.offset + sc._F_BLOCK.size + e.compressed_size,
                    ar._file_size - sc._FOOTER_SIZE,
                )

    def test_incompressible_data_still_roundtrips(self):
        data = bytes(random.Random(7).randrange(256) for _ in range(20000))
        self.assertRoundTrips(data, 5000)


class RandomReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="snapchunk-read-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.data = make_snapshot(99, 50000)
        self.dst = os.path.join(self.tmp, "a.snpk")
        sc.compress_snapshot(self.data, self.dst, block_size=4096)

    def test_range_within_one_block(self):
        self.assertEqual(sc.read_range(self.dst, 10, 50), self.data[10:60])

    def test_range_spanning_blocks(self):
        self.assertEqual(sc.read_range(self.dst, 4000, 5000),
                         self.data[4000:9000])

    def test_range_on_block_boundary(self):
        self.assertEqual(sc.read_range(self.dst, 4096, 4096),
                         self.data[4096:8192])

    def test_range_to_end_and_beyond(self):
        self.assertEqual(sc.read_range(self.dst, 49900, 99999),
                         self.data[49900:])
        self.assertEqual(sc.read_range(self.dst, 0), self.data)

    def test_negative_start_and_zero_length(self):
        self.assertEqual(sc.read_range(self.dst, -10, 10),
                         self.data[-10:])
        self.assertEqual(sc.read_range(self.dst, 100, 0), b"")

    def test_random_ranges_against_original(self):
        rng = random.Random(5)
        for _ in range(200):
            start = rng.randrange(0, len(self.data))
            length = rng.randrange(0, 30000)
            self.assertEqual(
                sc.read_range(self.dst, start, length),
                self.data[start : start + length],
            )


def flip_bytes(path, offset, count=1, seed=1):
    rng = random.Random(seed)
    with open(path, "r+b") as fh:
        fh.seek(offset)
        blob = bytearray(fh.read(count))
        for i in range(len(blob)):
            blob[i] ^= 1 << rng.randrange(8)
        fh.seek(offset)
        fh.write(blob)


class CorruptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="snapchunk-corrupt-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.data = make_snapshot(3, 30000)
        self.dst = os.path.join(self.tmp, "a.snpk")
        sc.compress_snapshot(self.data, self.dst, block_size=4096)

    def test_payload_corruption_is_located(self):
        with sc.Archive(self.dst) as ar:
            target = 4
            off = block_payload_offset(ar, target)
            self.assertGreater(ar.entries[target].compressed_size, 2)
        flip_bytes(self.dst, off + 1, 1)

        with sc.Archive(self.dst) as ar:
            report = ar.verify()
            self.assertFalse(report.ok)
            self.assertEqual(report.bad_blocks, [target])
            # reading a healthy block still works
            self.assertEqual(ar.read_block(0), self.data[:4096])
            with self.assertRaises(sc.BlockCorruptionError) as ctx:
                ar.read_block(target)
        err = ctx.exception
        self.assertEqual(err.block_index, target)
        self.assertIsNotNone(err.expected)
        self.assertNotEqual(err.actual, err.expected)

    def test_corrupt_block_refuses_wrong_data(self):
        with sc.Archive(self.dst) as ar:
            target, off = 2, block_payload_offset(ar, 2)
        flip_bytes(self.dst, off, 2)
        with sc.Archive(self.dst) as ar:
            try:
                ar.read_block(target)
                raised = None
            except sc.BlockCorruptionError as exc:
                raised = exc
            self.assertIsNotNone(raised)
            # Range overlapping the corrupt block must not return bad bytes.
            with self.assertRaises(sc.BlockCorruptionError):
                ar.read_range(2 * 4096, 10)
            # Range in a neighboring block is unaffected.
            self.assertEqual(ar.read_range(0, 10), self.data[:10])

    def test_block_header_corruption_detected(self):
        with sc.Archive(self.dst) as ar:
            target, off = 3, ar.entries[3].offset
        flip_bytes(self.dst, off, 4)
        with sc.Archive(self.dst) as ar:
            with self.assertRaises(sc.BlockCorruptionError) as ctx:
                ar.read_block(target)
            self.assertEqual(ctx.exception.block_index, target)
            self.assertEqual(ar.verify().bad_blocks, [target])

    def test_multiple_corrupt_blocks_all_located(self):
        with sc.Archive(self.dst) as ar:
            targets = [1, 5, 6]
            offsets = [block_payload_offset(ar, t) + 1 for t in targets]
        for i, off in enumerate(offsets):
            flip_bytes(self.dst, off, 1, seed=i)
        with sc.Archive(self.dst) as ar:
            self.assertEqual(ar.verify().bad_blocks, targets)

    def test_index_corruption_rejected(self):
        size = os.path.getsize(self.dst)
        # Flip a byte inside the index region.
        index_start = size - sc._FOOTER_SIZE - sc.ENTRY_SIZE * (
            sc._num_blocks(len(self.data), 4096)
        )
        flip_bytes(self.dst, index_start + 5, 1)
        with self.assertRaises(sc.IndexCorruptionError):
            sc.Archive(self.dst)

    def test_footer_magic_corruption(self):
        size = os.path.getsize(self.dst)
        flip_bytes(self.dst, size - 1, 1)
        with self.assertRaises(sc.FormatError):
            sc.Archive(self.dst)

    def test_truncated_file(self):
        with open(self.dst, "r+b") as fh:
            fh.truncate(20)
        with self.assertRaises(sc.FormatError):
            sc.Archive(self.dst)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="snapchunk-recover-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.data = make_snapshot(11, 40000)
        self.block_size = 4096
        self.primary = os.path.join(self.tmp, "primary.snpk")
        self.replica = os.path.join(self.tmp, "replica.snpk")
        sc.compress_snapshot(self.data, self.primary, self.block_size)
        shutil.copy2(self.primary, self.replica)

    def test_restore_block_from_replica(self):
        with sc.Archive(self.primary) as ar:
            target, off = 7, block_payload_offset(ar, 7)
        flip_bytes(self.primary, off + 2, 3)
        with sc.Archive(self.primary) as ar:
            self.assertEqual(ar.verify().bad_blocks, [target])

        repaired = sc.restore_block(self.primary, target,
                                    [self.replica])
        self.assertEqual(repaired, 1)
        # Full lossless round-trip after repair.
        self.assertEqual(sc.extract_snapshot(self.primary), self.data)
        self.assertTrue(sc.verify_archive(self.primary).ok)

    def test_recover_archive_repairs_and_counts(self):
        with sc.Archive(self.primary) as ar:
            for t in (2, 9):
                flip_bytes(self.primary, block_payload_offset(ar, t), 1,
                           seed=t)
        n = sc.recover_archive(self.primary, [self.replica])
        self.assertEqual(n, 2)
        self.assertEqual(sc.extract_snapshot(self.primary), self.data)

    def test_pick_healthy_replica_when_first_is_also_bad(self):
        # Both primary and one replica have block 4 corrupt; second replica
        # is healthy and must be selected automatically.
        rep2 = os.path.join(self.tmp, "replica2.snpk")
        shutil.copy2(self.replica, rep2)
        with sc.Archive(self.primary) as ar:
            off = block_payload_offset(ar, 4)
        flip_bytes(self.primary, off, 1)
        flip_bytes(self.replica, off, 1, seed=9)
        n = sc.recover_archive(self.primary, [self.replica, rep2])
        self.assertEqual(n, 1)
        self.assertTrue(sc.verify_archive(self.primary).ok)

    def test_all_copies_bad_names_the_block(self):
        with sc.Archive(self.primary) as ar:
            off = block_payload_offset(ar, 5)
        flip_bytes(self.primary, off, 1)
        flip_bytes(self.replica, off, 1, seed=4)
        with self.assertRaises(sc.BlockCorruptionError) as ctx:
            sc.recover_archive(self.primary, [self.replica])
        self.assertEqual(ctx.exception.block_index, 5)

    def test_recover_from_corrupted_primary_index(self):
        # Wipe part of the primary footer; replica index drives recovery.
        size = os.path.getsize(self.primary)
        with open(self.primary, "r+b") as fh:
            fh.seek(size - 10)
            fh.write(b"\xff" * 10)
        with self.assertRaises(sc.FormatError):
            sc.Archive(self.primary)
        sc.recover_archive(self.primary, [self.replica])
        self.assertEqual(sc.extract_snapshot(self.primary), self.data)

    def test_mismatched_replica_rejected(self):
        other = os.path.join(self.tmp, "other.snpk")
        sc.compress_snapshot(b"different content entirely", other, 4096)
        with self.assertRaises(ValueError):
            sc.recover_archive(self.primary, [other])


if __name__ == "__main__":
    unittest.main(verbosity=2)
