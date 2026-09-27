"""Throughput benchmark for the chunked decoder.

Run: python3 bench_chunked.py
"""

import gc
import time

from chunked_decoder import ChunkedDecoder, encode_chunked

PAYLOAD_MB = 64
FEED_SIZES = [("whole stream", None), ("1 MiB", 1 << 20),
              ("64 KiB", 1 << 16), ("4 KiB", 1 << 12)]
CHUNK_SIZES = [("64 KiB chunks", 1 << 16), ("8 KiB chunks", 1 << 12),
               ("1 KiB chunks", 1 << 10)]


def bench(stream: bytes, feed_size):
    dec = ChunkedDecoder()
    out = 0
    start = time.perf_counter()
    if feed_size is None:
        out += len(dec.feed(stream))
    else:
        for pos in range(0, len(stream), feed_size):
            out += len(dec.feed(stream[pos:pos + feed_size]))
    out += len(dec.finish())
    elapsed = time.perf_counter() - start
    return out, elapsed


def main():
    import random
    payload = random.Random(42).randbytes(PAYLOAD_MB * 1024 * 1024)
    print(f"payload: {PAYLOAD_MB} MiB, Python {__import__('sys').version.split()[0]}")
    print(f"{'encoding':<16}{'feed':<14}{'rounds':>6}{'best MB/s':>12}")
    for clabel, csize in CHUNK_SIZES:
        stream = encode_chunked(payload, csize)
        for flabel, fsize in FEED_SIZES:
            gc.disable()
            try:
                best = 0.0
                total = 0
                for _ in range(3):
                    n, elapsed = bench(stream, fsize)
                    assert n == len(payload)
                    total += 1
                    best = max(best, n / elapsed / 1e6)
            finally:
                gc.enable()
            print(f"{clabel:<16}{flabel:<14}{total:>6}{best:>12.1f}")
    # pathological: byte-at-a-time feed on a 4 MiB stream
    small = encode_chunked(payload[:4 * 1024 * 1024], 1 << 16)
    dec = ChunkedDecoder()
    n = 0
    start = time.perf_counter()
    for pos in range(len(small)):
        n += len(dec.feed(small[pos:pos + 1]))
    n += len(dec.finish())
    elapsed = time.perf_counter() - start
    print(f"{'64 KiB chunks':<16}{'1 byte':<14}{1:>6}{n / elapsed / 1e6:>12.1f}")


if __name__ == "__main__":
    main()
