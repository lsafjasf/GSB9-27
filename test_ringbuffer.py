"""Self-tests for ringbuffer: overwrite semantics, overrun reporting,
multi-reader independence, and concurrent single-writer/multi-reader
integrity (no torn records, no silent misalignment)."""

import threading
import time
import unittest

from ringbuffer import OverrunError, RingBuffer


def make_record(seq):
    # Self-checking record: every field must equal seq.
    return (seq, seq, seq, f"payload-{seq}")


def check_record(seq, record):
    return record == make_record(seq)


class TestOverwriteSemantics(unittest.TestCase):
    def test_capacity_one(self):
        buf = RingBuffer(1)
        r = buf.reader()
        self.assertIsNone(r.read())
        for i in range(1, 6):
            self.assertEqual(buf.write(make_record(i)), i)
        # Only the newest record survives; seqs 1..4 must be reported lost.
        with self.assertRaises(OverrunError) as ctx:
            r.read()
        self.assertEqual((ctx.exception.lost_from, ctx.exception.lost_to), (1, 4))
        seq, rec = r.read()
        self.assertEqual(seq, 5)
        self.assertTrue(check_record(seq, rec))
        self.assertIsNone(r.read())

    def test_write_far_beyond_capacity(self):
        cap = 8
        total = cap * 10000
        buf = RingBuffer(cap)
        r = buf.reader()
        for i in range(1, total + 1):
            buf.write(make_record(i))
        with self.assertRaises(OverrunError) as ctx:
            r.read()
        self.assertEqual(ctx.exception.lost_from, 1)
        self.assertEqual(ctx.exception.lost_to, total - cap)
        # The last `cap` records are intact and in order.
        for expected in range(total - cap + 1, total + 1):
            seq, rec = r.read()
            self.assertEqual(seq, expected)
            self.assertTrue(check_record(seq, rec))
        self.assertIsNone(r.read())

    def test_no_overrun_when_keeping_up(self):
        buf = RingBuffer(4)
        r = buf.reader()
        for i in range(1, 1001):
            buf.write(make_record(i))
            seq, rec = r.read()
            self.assertEqual(seq, i)
            self.assertTrue(check_record(seq, rec))

    def test_stalled_reader_catches_up(self):
        cap = 16
        buf = RingBuffer(cap)
        r = buf.reader()
        for i in range(1, 6):
            buf.write(make_record(i))
        # Reader stalls; writer laps it several times.
        for i in range(6, 6 + cap * 5):
            buf.write(make_record(i))
        write_seq = buf.write_seq
        with self.assertRaises(OverrunError) as ctx:
            r.read()
        self.assertEqual(ctx.exception.lost_from, 1)
        self.assertEqual(ctx.exception.lost_to, write_seq - cap)
        # After the overrun, the reader delivers every surviving record.
        for expected in range(write_seq - cap + 1, write_seq + 1):
            seq, rec = r.read()
            self.assertEqual(seq, expected)
            self.assertTrue(check_record(seq, rec))
        self.assertIsNone(r.read())

    def test_multi_reader_same_range(self):
        cap = 32
        buf = RingBuffer(cap)
        readers = [buf.reader() for _ in range(4)]
        n = 500
        for i in range(1, n + 1):
            buf.write(make_record(i))
        # n > cap, so every reader necessarily loses records to overrun;
        # each must report the exact lost ranges instead of hiding them.
        results = []
        for r in readers:
            seen = []
            lost = 0
            expected_next = 1
            while True:
                try:
                    item = r.read()
                except OverrunError as e:
                    # The lost range must start exactly where the cursor was;
                    # count it as lost, never as seen.
                    self.assertEqual(e.lost_from, expected_next)
                    self.assertGreaterEqual(e.lost_to, e.lost_from)
                    lost += e.lost_to - e.lost_from + 1
                    expected_next = e.lost_to + 1
                    continue
                if item is None:
                    break
                seq, rec = item
                self.assertTrue(check_record(seq, rec))
                self.assertEqual(seq, expected_next)
                seen.append(seq)
                expected_next = seq + 1
            # Seen records plus reported losses must account for 1..n exactly.
            self.assertEqual(expected_next, n + 1)
            self.assertEqual(len(seen) + lost, n)
            self.assertEqual(lost, n - cap)
            results.append(seen)
        # All readers are independent cursors over the same data: identical views.
        for seen in results:
            self.assertEqual(seen, results[0])


class TestConcurrency(unittest.TestCase):
    def test_single_writer_multi_reader_no_torn_reads(self):
        cap = 64
        buf = RingBuffer(cap)
        n_readers = 4
        stop = threading.Event()
        errors = []
        error_lock = threading.Lock()

        def writer():
            seq = 0
            while not stop.is_set():
                seq += 1
                buf.write(make_record(seq))

        def reader():
            r = buf.reader()
            expected_next = r.next_seq
            try:
                while not stop.is_set():
                    try:
                        item = r.read()
                    except OverrunError as e:
                        # Lost range must start exactly where we were.
                        if e.lost_from != expected_next or e.lost_to < e.lost_from:
                            with error_lock:
                                errors.append(
                                    f"bad overrun range [{e.lost_from}, {e.lost_to}] "
                                    f"expected_next={expected_next}"
                                )
                        expected_next = e.lost_to + 1
                        continue
                    if item is None:
                        continue
                    seq, rec = item
                    if seq != expected_next:
                        with error_lock:
                            errors.append(f"gap: expected {expected_next}, got {seq}")
                    if not check_record(seq, rec):
                        with error_lock:
                            errors.append(f"torn record at seq {seq}: {rec!r}")
                    expected_next = seq + 1
            except Exception as exc:  # pragma: no cover
                with error_lock:
                    errors.append(f"reader crashed: {exc!r}")

        threads = [threading.Thread(target=writer)]
        threads += [threading.Thread(target=reader) for _ in range(n_readers)]
        for t in threads:
            t.start()
        time.sleep(2.0)
        stop.set()
        for t in threads:
            t.join()

        self.assertGreater(buf.write_seq, 1000, "writer made no progress")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
