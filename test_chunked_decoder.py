"""Self-tests for chunked_decoder.  Run: python3 test_chunked_decoder.py -v"""

import random
import unittest

from chunked_decoder import (
    ChunkedDecodeError,
    ChunkedDecoder,
    encode_chunked,
)


def decode_in_pieces(stream: bytes, sizes):
    """Feed *stream* to a decoder in pieces of the given sizes."""
    dec = ChunkedDecoder()
    out = bytearray()
    pos = 0
    for size in sizes:
        piece = stream[pos:pos + size]
        out += dec.feed(piece)
        pos += len(piece)
    assert pos == len(stream)
    out += dec.finish()
    assert dec.done
    return bytes(out), dec


def all_splits(stream: bytes, n: int):
    """All ways to cut *stream* into *n* contiguous pieces (positions)."""
    if n == 1:
        yield [len(stream)]
        return
    for first in range(0, len(stream) + 1):
        for rest in all_splits(stream[first:], n - 1):
            yield [first] + rest


class RoundTripTest(unittest.TestCase):
    def test_basic_round_trip(self):
        payload = b"hello chunked world" * 100
        out, _ = decode_in_pieces(encode_chunked(payload, 64), [10 ** 9])
        self.assertEqual(out, payload)

    def test_empty_message(self):
        out, dec = decode_in_pieces(b"0\r\n\r\n", [5])
        self.assertEqual(out, b"")
        self.assertEqual(dec.trailers, [])

    def test_terminal_chunk_with_extension(self):
        out, dec = decode_in_pieces(b"3\r\nabc\r\n0;final=true\r\n\r\n", [100])
        self.assertEqual(out, b"abc")
        self.assertEqual(dec.chunks[-1], (0, ((b"final", b"true"),)))

    def test_trailers_captured(self):
        stream = (b"4\r\nWiki\r\n5\r\npedia\r\n0\r\n"
                  b"X-Checksum: deadbeef\r\nExpires: never\r\n\r\n")
        out, dec = decode_in_pieces(stream, [len(stream)])
        self.assertEqual(out, b"Wikipedia")
        self.assertEqual(dec.trailers,
                         [(b"X-Checksum", b"deadbeef"), (b"Expires", b"never")])

    def test_extensions_parsed(self):
        stream = (b'a;foo=bar;baz;quoted="a;b\\"c"\r\n'
                  b"0123456789\r\n0\r\n\r\n")
        out, dec = decode_in_pieces(stream, [len(stream)])
        self.assertEqual(out, b"0123456789")
        self.assertEqual(dec.chunks[0][1],
                         ((b"foo", b"bar"), (b"baz", None),
                          (b"quoted", b'a;b"c')))

    def test_single_byte_chunks(self):
        payload = bytes(range(256))
        stream = b"".join(b"1\r\n" + bytes([b]) + b"\r\n" for b in payload)
        stream += b"0\r\n\r\n"
        out, _ = decode_in_pieces(stream, [len(stream)])
        self.assertEqual(out, payload)

    def test_many_chunks_one_feed(self):
        payload = bytes(random.Random(1).randbytes(100_000))
        stream = encode_chunked(payload, 7)  # ~14k chunks, fed all at once
        out, _ = decode_in_pieces(stream, [len(stream)])
        self.assertEqual(out, payload)

    def test_large_chunk(self):
        payload = random.Random(2).randbytes(8 * 1024 * 1024)
        stream = encode_chunked(payload, 8 * 1024 * 1024)
        out, _ = decode_in_pieces(stream, [len(stream)])
        self.assertEqual(out, payload)

    def test_chunk_size_limit(self):
        dec = ChunkedDecoder(max_chunk_size=16)
        with self.assertRaises(ChunkedDecodeError) as cm:
            dec.feed(b"11\r\n")  # 0x11 = 17 > 16
        self.assertIn("exceeds limit", cm.exception.message)
        self.assertEqual(cm.exception.offset, 0)


class EquivalenceTest(unittest.TestCase):
    """Chunking independence: any slicing of the same encoded stream must
    produce byte-identical output."""

    STREAM = (b"5;ext=1\r\nHello\r\n"
              b"6;foo=bar;baz\r\n world\r\n"
              b"1\r\n!\r\n"
              b"0;end=yes\r\nX-T: 1\r\n\r\n")
    EXPECTED = b"Hello world!"

    def check(self, sizes):
        out, dec = decode_in_pieces(self.STREAM, sizes)
        self.assertEqual(out, self.EXPECTED)
        self.assertEqual(dec.trailers, [(b"X-T", b"1")])

    def test_every_two_part_split(self):
        for cut in range(len(self.STREAM) + 1):
            with self.subTest(cut=cut):
                self.check([cut, len(self.STREAM) - cut])

    def test_every_three_part_split(self):
        for sizes in all_splits(self.STREAM, 3):
            with self.subTest(sizes=sizes):
                self.check(sizes)

    def test_byte_at_a_time(self):
        self.check([1] * len(self.STREAM))

    def test_random_splits_random_payloads(self):
        rng = random.Random(20260928)
        for trial in range(50):
            payload = rng.randbytes(rng.randrange(0, 5000))
            n_chunks = rng.randrange(1, 20)
            # encode with random chunk boundaries
            stream = bytearray()
            pos = 0
            for _ in range(n_chunks):
                end = min(len(payload), pos + rng.randrange(1, 600))
                piece = payload[pos:end]
                if piece:
                    stream += f"{len(piece):x};t={len(piece)}\r\n".encode()
                    stream += piece + b"\r\n"
                pos = end
            if pos < len(payload):
                rest = payload[pos:]
                stream += f"{len(rest):x}\r\n".encode() + rest + b"\r\n"
            stream += b"0\r\nX-End: 1\r\n\r\n"
            stream = bytes(stream)
            # reference: single feed
            ref, _ = decode_in_pieces(stream, [len(stream)])
            self.assertEqual(ref, payload)
            # random feed splits must agree byte-for-byte
            for _ in range(10):
                cuts = sorted(rng.randrange(0, len(stream) + 1)
                              for _ in range(rng.randrange(1, 30)))
                sizes = [b - a for a, b in zip([0] + cuts, cuts + [len(stream)])]
                sizes = [s for s in sizes if s]
                out, _ = decode_in_pieces(stream, sizes)
                self.assertEqual(out, ref, f"trial={trial} sizes={sizes}")


class ErrorTest(unittest.TestCase):
    """Malformed input must raise ChunkedDecodeError with a stream offset."""

    def expect_error(self, stream, offset, *fragments, limit=None):
        dec = ChunkedDecoder(max_chunk_size=limit or 64 * 1024 * 1024)
        with self.assertRaises(ChunkedDecodeError) as cm:
            dec.feed(stream)
            dec.finish()
        err = cm.exception
        self.assertEqual(err.offset, offset,
                         f"{err.message}: want offset {offset}")
        for frag in fragments:
            self.assertIn(frag, err.message)
        return err

    # -- invalid length lines
    def test_non_hex_size(self):
        self.expect_error(b"Z\r\n", 0, "invalid character", "chunk size")

    def test_non_hex_size_midline(self):
        self.expect_error(b"12x4\r\n", 2, "invalid character")

    def test_empty_size(self):
        self.expect_error(b"\r\n", 0, "empty chunk size")

    def test_size_line_too_long(self):
        dec = ChunkedDecoder(max_line=8)
        with self.assertRaises(ChunkedDecodeError) as cm:
            dec.feed(b"012345678")  # 9 bytes, no CRLF yet
        self.assertIn("too long", cm.exception.message)
        self.assertEqual(cm.exception.offset, 0)

    # -- missing terminal chunk
    def test_missing_terminal_chunk(self):
        self.expect_error(b"3\r\nabc\r\n", 8, "missing terminal zero-size")

    def test_missing_terminal_empty_stream(self):
        self.expect_error(b"", 0, "missing terminal zero-size")

    # -- chunk size vs actual data
    def test_eof_inside_chunk_data(self):
        self.expect_error(b"5\r\nabc", 6,
                          "inside chunk data", "declared 5", "received 3")

    def test_bad_crlf_after_data(self):
        self.expect_error(b"3\r\nabcXX", 6, "expected CRLF", "b'XX'")

    def test_bad_crlf_after_data_offset(self):
        # error offset must point at the byte following the chunk payload
        self.expect_error(b"2\r\nab\rX", 5, "expected CRLF")

    # -- extension syntax
    def test_ext_missing_name(self):
        self.expect_error(b"3;=x\r\n", 2, "extension name")

    def test_ext_missing_value(self):
        self.expect_error(b"3;foo=\r\n", 6, "extension value")

    def test_ext_stray_char(self):
        self.expect_error(b"3;foo bar\r\n", 6, "unexpected character")

    def test_ext_unterminated_quote(self):
        self.expect_error(b'3;foo="abc\r\n', 10, "unterminated quoted-string")

    def test_ext_bad_char_in_quote(self):
        self.expect_error(b'3;foo="a\x01b"\r\n', 8, "invalid character")

    # -- trailers
    def test_bad_trailer_no_colon(self):
        self.expect_error(b"0\r\nnot-a-header\r\n\r\n", 3, "malformed trailer")

    def test_bad_trailer_name(self):
        self.expect_error(b"0\r\nBad Name: x\r\n\r\n", 6,
                          "invalid character", "trailer")

    def test_eof_inside_trailers(self):
        self.expect_error(b"0\r\nX: 1\r\n", 9, "inside trailer section")

    # -- extra data after terminal chunk
    def test_trailing_data(self):
        self.expect_error(b"0\r\n\r\njunk", 5, "trailing data")

    def test_error_offsets_demo(self):
        """Print a table of error-location samples (also serves as doc)."""
        cases = [
            ("invalid length", b"1Z\r\n"),
            ("missing terminal", b"3\r\nabc\r\n"),
            ("size != data", b"5\r\nabc"),
            ("bad ext", b"3;foo bar\r\n"),
        ]
        rows = []
        for label, stream in cases:
            dec = ChunkedDecoder()
            try:
                dec.feed(stream)
                dec.finish()
                self.fail(f"{label}: no error raised")
            except ChunkedDecodeError as err:
                rows.append((label, stream, err.offset, err.message))
        print("\n--- error location samples ---")
        for label, stream, offset, msg in rows:
            print(f"{label:18} input={stream!r:24} offset={offset:2}  {msg}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
