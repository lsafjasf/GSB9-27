"""Self-tests for the chunked, checksummed snapshot store.

Run:  python3 -m unittest -v test_snapshotstore
(or)  python3 test_snapshotstore.py
"""

import io
import os
import random
import struct
import tempfile
import unittest
import zlib

import snapshotstore as ss


def make_payload(n_bytes: int, seed: int = 1234) -> bytes:
    """Deterministic, *moderately* compressible payload."""
    rnd = random.Random(seed)
    vocab = [b"STATE|", b"key=", b"value=", b"\x00\x00", b"blob", b"|"]
    out = bytearray()
    while len(out) < n_bytes:
        if rnd.random() < 0.7:
            out += vocab[rnd.randrange(len(vocab))]
            out += b"%d" % rnd.randrange(1000)
        else:
            out += bytes(rnd.getrandbits(8) for _ in range(rnd.randrange(1, 8)))
    return bytes(out[:n_bytes])


class SnapshotTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def p(self, name):
        return os.path.join(self.tmp, name)


class RoundTripTests(SnapshotTestBase):
    def assert_roundtrip(self, data: bytes, chunk_size: int):
        path = self.p("snap.bin")
        stats = ss.write_snapshot(path, data, chunk_size=chunk_size)
        self.assertEqual(stats["original_size"], len(data))
        with ss.SnapshotReader(path) as rd:
            # Lossless, byte-for-byte assertion required by the spec.
            self.assertEqual(rd.read_all(), data)
            self.assertEqual(rd.original_size, len(data))
            self.assertEqual(rd.verify(), [])

    def test_empty_snapshot(self):
        path = self.p("empty.bin")
        stats = ss.write_snapshot(path, b"", chunk_size=1024)
        self.assertEqual(stats["chunks"], 0)
        with ss.SnapshotReader(path) as rd:
            self.assertEqual(rd.read_all(), b"")
            self.assertEqual(rd.read_range(0, 0), b"")
            self.assertEqual(rd.read_range(0, end=0), b"")
            self.assertEqual(rd.verify(), [])
            self.assertEqual(len(rd.chunks), 0)
            with self.assertRaises(IndexError):
                rd.read_chunk(0)

    def test_single_chunk_smaller_than_chunk_size(self):
        data = make_payload(500)
        self.assert_roundtrip(data, 4096)

    def test_single_chunk_exact_size(self):
        data = make_payload(4096)
        self.assert_roundtrip(data, 4096)

    def test_chunk_boundaries_unaligned_size(self):
        # 3 full chunks + one partial tail.
        data = make_payload(3 * 1024 + 37)
        self.assert_roundtrip(data, 1024)

    def test_many_chunks_over_one_thousand(self):
        n_chunks = 1200
        chunk_size = 1024
        data = make_payload(n_chunks * chunk_size + 5)
        stats = ss.write_snapshot(self.p("many.bin"), data,
                                  chunk_size=chunk_size)
        self.assertEqual(stats["chunks"], n_chunks + 1)
        with ss.SnapshotReader(self.p("many.bin")) as rd:
            self.assertEqual(rd.verify(), [])
            self.assertEqual(rd.read_all(), data)

    def test_stream_input_roundtrip(self):
        data = make_payload(50_000)
        stats = ss.write_snapshot(self.p("stream.bin"),
                                  io.BytesIO(data), chunk_size=4096)
        with ss.SnapshotReader(self.p("stream.bin")) as rd:
            self.assertEqual(rd.read_all(), data)
            self.assertGreaterEqual(stats["chunks"], 12)

    def test_reopened_reader_persistent_index(self):
        data = make_payload(20_000)
        path = self.p("reopen.bin")
        ss.write_snapshot(path, data, chunk_size=5000)
        rd1 = ss.SnapshotReader(path)
        self.assertEqual(rd1.read_range(7000, 100), data[7000:7100])
        rd1.close()
        with ss.SnapshotReader(path) as rd2:
            self.assertEqual(rd2.read_all(), data)

    def test_deterministic_compression_stats(self):
        data = make_payload(20_000, seed=7)
        s1 = ss.write_snapshot(self.p("a.bin"), data, chunk_size=4096)
        s2 = ss.write_snapshot(self.p("b.bin"), data, chunk_size=4096)
        self.assertEqual(s1["compressed_size"], s2["compressed_size"])
        with open(self.p("a.bin"), "rb") as a, open(self.p("b.bin"), "rb") as b:
            self.assertEqual(a.read(), b.read())


class RandomReadTests(SnapshotTestBase):
    def test_random_ranges_match_original(self):
        data = make_payload(100_000, seed=42)
        path = self.p("r.bin")
        ss.write_snapshot(path, data, chunk_size=4096)
        rnd = random.Random(99)
        with ss.SnapshotReader(path) as rd:
            for _ in range(200):
                start = rnd.randrange(0, len(data))
                end = rnd.randrange(start, len(data) + 1)
                self.assertEqual(rd.read_range(start, end=end), data[start:end])
                self.assertEqual(rd.read_range(start, length=end - start),
                                 data[start:end])

    def test_range_spanning_exact_boundaries(self):
        data = make_payload(10_000)
        path = self.p("b.bin")
        ss.write_snapshot(path, data, chunk_size=1000)
        with ss.SnapshotReader(path) as rd:
            self.assertEqual(rd.read_range(990, length=20), data[990:1010])
            self.assertEqual(rd.read_range(0, end=1000), data[:1000])
            self.assertEqual(rd.read_range(1000, end=2000), data[1000:2000])
            self.assertEqual(rd.read_range(0, length=0), b"")
            self.assertEqual(rd.read_range(len(data)), b"")
            with self.assertRaises(ValueError):
                rd.read_range(0, end=len(data) + 1)
            self.assertEqual(rd.locate(0), 0)
            self.assertEqual(rd.locate(1999), 1)


class CorruptionTests(SnapshotTestBase):
    def _flip_byte(self, path, offset):
        with open(path, "r+b") as fh:
            fh.seek(offset)
            b = fh.read(1)
            fh.seek(offset)
            fh.write(bytes([b[0] ^ 0xFF]))

    def test_corrupt_chunk_is_located_and_rejected(self):
        data = make_payload(20_000)
        path = self.p("c.bin")
        ss.write_snapshot(path, data, chunk_size=4096)
        with ss.SnapshotReader(path) as good:
            victim_offset = good.chunks[3].offset + 10
        self._flip_byte(path, victim_offset)

        with ss.SnapshotReader(path) as rd:
            # verify() names the exact bad chunk
            self.assertEqual(rd.verify(), [3])
            with self.assertRaises(ss.CorruptChunkError) as cm:
                rd.read_chunk(3)
            self.assertEqual(cm.exception.index, 3)
            with self.assertRaises(ss.CorruptChunkError) as cm:
                rd.read_range(3 * 4096, length=10)
            self.assertEqual(cm.exception.index, 3)
            # untouched chunks still read fine
            self.assertEqual(rd.read_chunk(0), data[:4096])
            self.assertEqual(rd.read_range(10, length=5), data[10:15])
            # read_all refuses to return partially wrong data
            with self.assertRaises(ss.CorruptChunkError):
                rd.read_all()

    def test_corruption_in_last_chunk(self):
        data = make_payload(9000)
        path = self.p("last.bin")
        ss.write_snapshot(path, data, chunk_size=4096)
        with ss.SnapshotReader(path) as good:
            off = good.chunks[2].offset
        self._flip_byte(path, off)
        with ss.SnapshotReader(path) as rd:
            self.assertEqual(rd.verify(), [2])
            self.assertEqual(rd.read_range(0, end=8192), data[:8192])

    def test_multiple_corrupt_chunks_all_reported(self):
        data = make_payload(30_000)
        path = self.p("multi.bin")
        ss.write_snapshot(path, data, chunk_size=4096)
        with ss.SnapshotReader(path) as good:
            victims = [good.chunks[i].offset for i in (1, 4, 5)]
        for off in victims:
            self._flip_byte(path, off)
        with ss.SnapshotReader(path) as rd:
            self.assertEqual(rd.verify(), [1, 4, 5])

    def test_corrupt_index_detected(self):
        data = make_payload(5000)
        path = self.p("idx.bin")
        ss.write_snapshot(path, data, chunk_size=4096)
        size = os.path.getsize(path)
        # flip a byte inside the JSON index region
        self._flip_byte(path, size - ss.TRAILER_SIZE - 5)
        with self.assertRaises(ss.CorruptSnapshotError):
            ss.SnapshotReader(path)

    def test_truncated_file_detected(self):
        data = make_payload(5000)
        path = self.p("trunc.bin")
        ss.write_snapshot(path, data, chunk_size=4096)
        size = os.path.getsize(path)
        with open(path, "r+b") as fh:
            fh.truncate(size - 10)
        with self.assertRaises(ss.CorruptSnapshotError):
            ss.SnapshotReader(path)

    def test_bad_magic_detected(self):
        path = self.p("magic.bin")
        ss.write_snapshot(path, b"hello")
        self._flip_byte(path, 0)
        with self.assertRaises(ss.CorruptSnapshotError):
            ss.SnapshotReader(path)


class ReplicaRepairTests(SnapshotTestBase):
    def test_restore_from_replica(self):
        data = make_payload(30_000, seed=5)
        primary = self.p("primary.bin")
        replica = self.p("replica.bin")
        ss.write_snapshot(primary, data, chunk_size=4096)
        ss.write_snapshot(replica, data, chunk_size=4096)

        with ss.SnapshotReader(primary) as good:
            victim = good.chunks[3].offset + 7
        with open(primary, "r+b") as fh:
            fh.seek(victim)
            b = fh.read(1)
            fh.seek(victim)
            fh.write(bytes([b[0] ^ 0xFF]))

        with ss.SnapshotReader(primary) as rd:
            self.assertEqual(rd.verify(), [3])

        repaired = ss.restore_chunks(primary, [replica], auto_repair=True)
        self.assertEqual(repaired, [3])

        with ss.SnapshotReader(primary) as rd:
            self.assertEqual(rd.verify(), [])
            self.assertEqual(rd.read_all(), data)

    def test_restore_with_different_compression_level(self):
        data = make_payload(30_000, seed=6)
        primary = self.p("p.bin")
        replica = self.p("r.bin")
        ss.write_snapshot(primary, data, chunk_size=4096, level=9)
        ss.write_snapshot(replica, data, chunk_size=4096, level=1)
        with ss.SnapshotReader(primary) as good:
            off = good.chunks[2].offset
        with open(primary, "r+b") as fh:
            fh.seek(off)
            bv = fh.read(1)
            fh.seek(off)
            fh.write(bytes([bv[0] ^ 0xFF]))

        repaired = ss.restore_chunks(primary, [replica], auto_repair=True)
        self.assertEqual(repaired, [2])
        with ss.SnapshotReader(primary) as rd:
            self.assertEqual(rd.read_all(), data)

    def test_restore_explicit_indices_and_picks_healthy_replica(self):
        data = make_payload(20_000, seed=8)
        primary = self.p("p.bin")
        bad_replica = self.p("rbad.bin")
        good_replica = self.p("rgood.bin")
        ss.write_snapshot(primary, data, chunk_size=4096)
        ss.write_snapshot(bad_replica, data, chunk_size=4096)
        ss.write_snapshot(good_replica, data, chunk_size=4096)
        # damage chunk 1 in primary AND in the first replica
        with ss.SnapshotReader(primary) as good:
            off = good.chunks[1].offset
        for target in (primary, bad_replica):
            with open(target, "r+b") as fh:
                fh.seek(off)
                bv = fh.read(1)
                fh.seek(off)
                fh.write(bytes([bv[0] ^ 0xFF]))
        repaired = ss.restore_chunks(primary, [bad_replica, good_replica],
                                     indices=[1], auto_repair=True)
        self.assertEqual(repaired, [1])
        with ss.SnapshotReader(primary) as rd:
            self.assertEqual(rd.read_all(), data)

    def test_restore_fails_when_no_healthy_replica(self):
        data = make_payload(20_000, seed=9)
        primary = self.p("p.bin")
        replica = self.p("r.bin")
        ss.write_snapshot(primary, data, chunk_size=4096)
        ss.write_snapshot(replica, data, chunk_size=4096)
        with ss.SnapshotReader(primary) as good:
            off_p = good.chunks[1].offset
            off_r = good.chunks[1].offset
        for path, off in ((primary, off_p), (replica, off_r)):
            with open(path, "r+b") as fh:
                fh.seek(off)
                bv = fh.read(1)
                fh.seek(off)
                fh.write(bytes([bv[0] ^ 0xFF]))
        with self.assertRaises(ss.MissingReplicaChunkError):
            ss.restore_chunks(primary, [replica], auto_repair=True)
        # failed repair must not have modified the primary
        with ss.SnapshotReader(primary) as rd:
            self.assertEqual(rd.verify(), [1])

    def test_replica_with_different_content_rejected(self):
        data = make_payload(20_000, seed=10)
        other = make_payload(20_000, seed=11)
        primary = self.p("p.bin")
        replica = self.p("r.bin")
        ss.write_snapshot(primary, data, chunk_size=4096)
        ss.write_snapshot(replica, other, chunk_size=4096)
        with ss.SnapshotReader(primary) as good:
            off = good.chunks[0].offset
        with open(primary, "r+b") as fh:
            fh.seek(off)
            bv = fh.read(1)
            fh.seek(off)
            fh.write(bytes([bv[0] ^ 0xFF]))
        with self.assertRaises(ss.MissingReplicaChunkError):
            ss.restore_chunks(primary, [replica], auto_repair=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
