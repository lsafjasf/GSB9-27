"""Overrun / concurrency tests for OverwriteRingBuffer (stdlib unittest)."""

import threading
import time
import unittest

from ring_buffer import OverwriteRingBuffer


def make_item(seq):
    # Redundant encoding so a torn/misaligned read is always detectable.
    return (seq, seq * 7, "record-%d" % seq)


def check_item(test, seq, item):
    test.assertEqual(item, make_item(seq), "torn or misaligned record")


def drain(reader, stop_seq):
    """Read until stop_seq (inclusive) is consumed; return list of results."""
    out = []
    while True:
        res = reader.poll()
        if res is None:
            if reader.cursor > stop_seq:
                return out
            time.sleep(0)
            continue
        out.append(res)


def assert_tiling(test, results, start, stop):
    """Received seqs + reported lost ranges must tile [start, stop] exactly."""
    expected = start
    received = 0
    for res in results:
        if res.lost is not None:
            lost_start, lost_end = res.lost
            test.assertEqual(lost_start, expected, "lost range must start at cursor")
            test.assertLessEqual(lost_start, lost_end)
            expected = lost_end + 1
        test.assertEqual(res.seq, expected, "gap not explained by a lost range")
        check_item(test, res.seq, res.item)
        expected = res.seq + 1
        received += 1
    test.assertEqual(expected, stop + 1)
    return received


class TestSequentialSemantics(unittest.TestCase):
    def test_capacity_one(self):
        buf = OverwriteRingBuffer(1)
        reader = buf.reader(start=0)
        for i in range(10):
            self.assertEqual(buf.write(make_item(i)), i)
        self.assertEqual(buf.latest_seq(), 9)
        self.assertEqual(buf.oldest_seq(), 9)
        res = reader.poll()
        self.assertEqual(res.seq, 9)
        self.assertEqual(res.lost, (0, 8))  # 0..8 overwritten
        check_item(self, 9, res.item)
        self.assertIsNone(reader.poll())

    def test_capacity_one_live_tail(self):
        buf = OverwriteRingBuffer(1)
        reader = buf.reader(start=0)
        buf.write(make_item(0))
        res = reader.poll()
        self.assertIsNone(res.lost)
        self.assertEqual(res.seq, 0)
        buf.write(make_item(1))
        buf.write(make_item(2))
        res = reader.poll()
        self.assertEqual(res.seq, 2)
        self.assertEqual(res.lost, (1, 1))

    def test_writes_far_exceed_capacity(self):
        capacity, total = 128, 100_000
        buf = OverwriteRingBuffer(capacity)
        reader = buf.reader(start=0)
        for i in range(total):
            buf.write(make_item(i))
        results = drain(reader, total - 1)
        self.assertEqual(len(results), capacity)
        self.assertEqual(results[0].lost, (0, total - capacity - 1))
        for idx, res in enumerate(results):
            self.assertEqual(res.seq, total - capacity + idx)
            check_item(self, res.seq, res.item)
            if idx:
                self.assertIsNone(res.lost)

    def test_no_overrun_when_reads_keep_up(self):
        buf = OverwriteRingBuffer(16)
        reader = buf.reader(start=0)
        for i in range(1000):
            buf.write(make_item(i))
            res = reader.poll()
            self.assertEqual(res.seq, i)
            self.assertIsNone(res.lost)

    def test_multi_reader_same_range(self):
        buf = OverwriteRingBuffer(2048)
        for i in range(1000):
            buf.write(make_item(i))
        readers = [buf.reader(start=0) for _ in range(3)]
        streams = [drain(r, 999) for r in readers]
        for stream in streams[1:]:
            self.assertEqual(stream, streams[0])
        self.assertEqual([r.seq for r in streams[0]], list(range(1000)))
        self.assertTrue(all(r.lost is None for r in streams[0]))

    def test_blocking_read_and_timeout(self):
        buf = OverwriteRingBuffer(4)
        reader = buf.reader(start=0)
        with self.assertRaises(TimeoutError):
            reader.read(timeout=0.05)

        def delayed_write():
            time.sleep(0.05)
            buf.write(make_item(0))

        t = threading.Thread(target=delayed_write)
        t.start()
        res = reader.read(timeout=2.0)
        t.join()
        self.assertEqual(res.seq, 0)

    def test_invalid_capacity(self):
        with self.assertRaises(ValueError):
            OverwriteRingBuffer(0)


class TestConcurrency(unittest.TestCase):
    def test_stalled_reader_catches_up(self):
        capacity, total = 256, 50_000
        buf = OverwriteRingBuffer(capacity)
        reader = buf.reader(start=0)
        done = threading.Event()

        def writer():
            for i in range(total):
                buf.write(make_item(i))
            done.set()

        t = threading.Thread(target=writer)
        t.start()
        time.sleep(0.2)  # reader stalls while writer overwrites heavily
        results = drain(reader, total - 1)
        t.join()
        self.assertTrue(done.is_set())
        self.assertEqual(results[0].lost[0], 0)
        self.assertGreater(results[0].lost[1], 0)  # stall really caused loss
        received = assert_tiling(self, results, 0, total - 1)
        self.assertLessEqual(received, capacity + 1)

    def test_single_writer_multi_reader_stress(self):
        capacity, total, n_readers = 64, 200_000, 4
        buf = OverwriteRingBuffer(capacity)
        writer_done = threading.Event()
        errors = []

        def writer():
            for i in range(total):
                buf.write(make_item(i))
            writer_done.set()

        def reader_fn(results, idx):
            try:
                r = buf.reader(start=0)
                while True:
                    res = r.poll()
                    if res is None:
                        if writer_done.is_set() and r.cursor > buf.latest_seq():
                            return
                        time.sleep(0)
                        continue
                    results[idx].append(res)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        results = [[] for _ in range(n_readers)]
        threads = [threading.Thread(target=reader_fn, args=(results, i))
                   for i in range(n_readers)]
        w = threading.Thread(target=writer)
        for t in threads:
            t.start()
        w.start()
        w.join()
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive(), "reader did not terminate")
        self.assertEqual(errors, [])
        for idx in range(n_readers):
            assert_tiling(self, results[idx], 0, total - 1)

    def test_blocking_readers_under_overwrite(self):
        capacity, total, n_readers = 8, 20_000, 3
        buf = OverwriteRingBuffer(capacity)
        results = [[] for _ in range(n_readers)]

        def reader_fn(idx):
            r = buf.reader(start=0)
            while True:
                try:
                    res = r.read(timeout=5.0)
                except TimeoutError:
                    return
                results[idx].append(res)
                if res.seq == total - 1:
                    return

        threads = [threading.Thread(target=reader_fn, args=(i,))
                   for i in range(n_readers)]
        for t in threads:
            t.start()
        for i in range(total):
            buf.write(make_item(i))
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive())
        for idx in range(n_readers):
            assert_tiling(self, results[idx], 0, total - 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
