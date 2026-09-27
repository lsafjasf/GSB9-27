"""Throughput benchmark for chunked_decoder."""

import random
import time

from chunked_decoder import ChunkedDecoder, decode


def encode(chunks):
    out = bytearray()
    for c in chunks:
        out += b"%x\r\n" % len(c) + c + b"\r\n"
    out += b"0\r\nX-Bench: done\r\n\r\n"
    return bytes(out)


def run(stream, feed_size):
    d = ChunkedDecoder()
    total = 0
    start = time.perf_counter()
    if feed_size is None:
        total = len(d.feed(stream))
    else:
        for i in range(0, len(stream), feed_size):
            total += len(d.feed(stream[i:i + feed_size]))
    d.finish()
    return time.perf_counter() - start, total


def main():
    payload = random.Random(42).randbytes(64 * 1024 * 1024)  # 64 MiB
    print(f"python {__import__('sys').version.split()[0]}, payload 64 MiB")
    for chunk_size in (1024, 16384, 65536):
        chunks = [payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size)]
        stream = encode(chunks)
        decode(stream)  # warm-up
        for feed_size in (None, 65536, 4096, 512):
            best = min(run(stream, feed_size)[0] for _ in range(3))
            mbps = len(stream) / best / 1e6
            label = "one-shot" if feed_size is None else f"feed {feed_size}B"
            print(f"chunk {chunk_size:>6}B | {label:>10} | {best*1e3:8.1f} ms | {mbps:8.1f} MB/s")


if __name__ == "__main__":
    main()
