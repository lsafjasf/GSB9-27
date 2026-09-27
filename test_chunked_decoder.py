"""Self-tests for chunked_decoder: streaming equivalence, error offsets, edge cases."""

import random
import sys
import unittest

from chunked_decoder import ChunkedDecodeError, ChunkedDecoder, decode


def encode(chunks, exts=None, trailers=()):
    """Build a chunked stream. exts[i] is the raw extension text for chunk i."""
    out = bytearray()
    for i, c in enumerate(chunks):
        ext = exts[i] if exts else b""
        out += b"%x" % len(c) + ext + b"\r\n" + c + b"\r\n"
    out += b"0\r\n"
    for name, value in trailers:
        out += name + b": " + value + b"\r\n"
    out += b"\r\n"
    return bytes(out)


def decode_in_pieces(stream, sizes):
    d = ChunkedDecoder()
    out = bytearray()
    pos = 0
    i = 0
    while pos < len(stream):
        n = sizes[i % len(sizes)]
        out += d.feed(stream[pos:pos + n])
        pos += n
        i += 1
    d.finish()
    return bytes(out)


class EquivalenceTest(unittest.TestCase):
    """Any segmentation of the same stream must decode byte-identically."""

    PAYLOADS = {
        "single": [b"Wikipedia"],
        "multi": [b"Hello, ", b"chunked ", b"world!" * 100, b"\x00\xff" * 500],
        "one_byte_chunks": [bytes([c]) for c in b"single-byte chunks!"],
        "empty_body": [],
        "binary": [bytes(range(256)) * 4],
    }

    def check_all_splits(self, stream, expect):
        self.assertEqual(decode(stream), expect)  # one-shot reference
        # every single split point
        for i in range(len(stream) + 1):
            d = ChunkedDecoder()
            got = d.feed(stream[:i]) + d.feed(stream[i:])
            d.finish()
            self.assertEqual(got, expect, f"split at {i}")
        # fixed piece sizes incl. byte-by-byte
        for n in (1, 2, 3, 5, 7, 16, 64, 4096):
            self.assertEqual(decode_in_pieces(stream, [n]), expect, f"piece size {n}")
        # random segmentations
        rng = random.Random(20260928)
        for _ in range(50):
            sizes = [rng.randint(1, 37) for _ in range(rng.randint(1, 20))]
            self.assertEqual(decode_in_pieces(stream, sizes), expect)

    def test_equivalence(self):
        for name, chunks in self.PAYLOADS.items():
            with self.subTest(payload=name):
                self.check_all_splits(encode(chunks), b"".join(chunks))

    def test_equivalence_with_extensions_and_trailers(self):
        chunks = [b"abc", b"defgh", b"x" * 1000]
        exts = [b";foo=bar", b";flag", b';k1=v1;k2="a;b=c";k3']
        trailers = [(b"X-Checksum", b"deadbeef"), (b"Expires", b"never")]
        stream = encode(chunks, exts=exts, trailers=trailers)
        self.check_all_splits(stream, b"".join(chunks))

    def test_zero_chunk_with_extension(self):
        stream = b"3\r\nabc\r\n0;reason=done\r\nX-T: 1\r\n\r\n"
        self.check_all_splits(stream, b"abc")

    def test_many_chunks_arriving_at_once(self):
        chunks = [b"chunk-%04d" % i for i in range(2000)]
        stream = encode(chunks)
        d = ChunkedDecoder()
        self.assertEqual(d.feed(stream), b"".join(chunks))  # one giant feed
        d.finish()
        self.assertTrue(d.done)


class EdgeCaseTest(unittest.TestCase):
    def test_empty_body(self):
        self.assertEqual(decode(b"0\r\n\r\n"), b"")

    def test_empty_body_with_trailers(self):
        self.assertEqual(decode(b"0\r\nA: 1\r\nB: 2\r\n\r\n"), b"")

    def test_single_byte_chunks(self):
        chunks = [b"a", b"b", b"c"]
        self.assertEqual(decode(encode(chunks)), b"abc")

    def test_large_chunk(self):
        blob = bytes(random.Random(7).randbytes(8 * 1024 * 1024))  # 8 MiB
        stream = encode([blob])
        self.assertEqual(decode(stream), blob)
        self.assertEqual(decode_in_pieces(stream, [65536]), blob)

    def test_uppercase_hex_and_ext_forms(self):
        stream = b"A;FLAG;X=1;Y=\"quoted ; v\"\r\n" + b"0123456789\r\n0\r\n\r\n"
        self.assertEqual(decode(stream), b"0123456789")

    def test_max_chunk_size_limit(self):
        with self.assertRaises(ChunkedDecodeError):
            decode(encode([b"x" * 100]), max_chunk_size=50)
        self.assertEqual(decode(encode([b"x" * 100]), max_chunk_size=100), b"x" * 100)

    def test_feed_after_done(self):
        d = ChunkedDecoder()
        d.feed(b"0\r\n\r\n")
        with self.assertRaises(ChunkedDecodeError):
            d.feed(b"x")


class ErrorOffsetTest(unittest.TestCase):
    def assert_error(self, stream, offset, needle, feed_size=None, finish=False):
        d = ChunkedDecoder()
        try:
            if feed_size:
                for i in range(0, len(stream), feed_size):
                    d.feed(stream[i:i + feed_size])
            else:
                d.feed(stream)
            if finish:
                d.finish()
        except ChunkedDecodeError as e:
            self.assertEqual(e.offset, offset, str(e))
            self.assertIn(needle, e.message)
            return
        self.fail(f"no error raised for {stream!r}")

    def test_invalid_length(self):
        self.assert_error(b"3\r\nabc\r\nZZ\r\n", 8, "invalid chunk length")
        self.assert_error(b"\r\n", 0, "invalid chunk length")

    def test_missing_terminal_chunk(self):
        self.assert_error(b"3\r\nabc\r\n", 8, "missing terminal", finish=True)
        self.assert_error(b"3\r\nab", 5, "missing terminal", finish=True)
        self.assert_error(b"", 0, "missing terminal", finish=True)

    def test_size_data_mismatch(self):
        # declares 5, sends 3 bytes + CRLF (consumed as data) -> CRLF expected at offset 8
        self.assert_error(b"5\r\nabc\r\n0\r\n\r\n", 8, "does not match")
        # declares 2, sends 3 bytes -> error where CRLF should be
        self.assert_error(b"2\r\nabc\r\n0\r\n\r\n", 5, "does not match")

    def test_bad_extension(self):
        self.assert_error(b"3;=novalue-name\r\n", 1, "malformed chunk extension")
        self.assert_error(b"3;foo=\r\n", 1, "malformed chunk extension")
        self.assert_error(b"3;foo=bar baz\r\n", 1, "malformed chunk extension")
        self.assert_error(b"3 ;spaced\r\n", 1, "malformed chunk extension")

    def test_bad_trailer(self):
        self.assert_error(b"0\r\nno colon here\r\n\r\n", 3, "malformed trailer")

    def test_offset_is_absolute_across_feeds(self):
        # error must carry the absolute offset even when fed byte-by-byte
        self.assert_error(b"4\r\nWiki\r\nXYZ\r\n", 9, "invalid chunk length", feed_size=1)

    def test_error_offsets_stable_under_any_split(self):
        stream = b"2\r\nabc\r\n0\r\n\r\n"  # mismatch detected at offset 5
        for n in (1, 2, 3, 4, 100):
            self.assert_error(stream, 5, "does not match", feed_size=n)


def format_offset_demo():
    """Human-readable error-location samples (also used by README)."""
    samples = [
        b"4\r\nWiki\r\nZZ\r\n",
        b"5\r\nabc\r\n0\r\n\r\n",
        b"3;foo=bar baz\r\nabc\r\n0\r\n\r\n",
        b"3\r\nabc\r\n",
    ]
    lines = []
    for s in samples:
        d = ChunkedDecoder()
        try:
            d.feed(s)
            d.finish()
            lines.append(f"{s!r}: OK")
        except ChunkedDecodeError as e:
            lines.append(f"{s!r}\n  -> {e}\n     offset {e.offset} = byte {s[e.offset:e.offset+1]!r}")
    return "\n".join(lines)


class DemoTest(unittest.TestCase):
    def test_demo_runs(self):
        text = format_offset_demo()
        self.assertIn("offset", text)
        if "-v" in sys.argv:
            print("\n" + text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
