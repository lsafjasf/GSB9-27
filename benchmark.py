"""Benchmark: compression ratio + random-read latency for snapchunk.

Generates a ~20 MiB mixed snapshot (zero regions / repetitive text / random
bytes, similar to a state snapshot with sparse + structured pages), writes
archives at several block sizes, and measures:

  * compressed size and ratio
  * full sequential extract throughput
  * verify (read+checksum every block) throughput
  * random-read latency, split into:
      - cold cache (fresh Archive, single small range)
      - warm cache (repeated range after block is cached)
  * cross-block range read

Run:  python3 benchmark.py
"""

import os
import random
import shutil
import tempfile
import time

import snapchunk as sc

SIZE = 20 * 1024 * 1024
LEVEL = 6
REPS = 200


def make_snapshot(size, seed=2024):
    rng = random.Random(seed)
    parts = (
        b"\x00" * (size // 3),
        (b"STATE_PAGE v7 seq=000001 " * 200_000)[: size // 3],
        bytes(rng.randrange(256) for _ in range(size - 2 * (size // 3))),
    )
    return b"".join(parts)


def timed(fn):
    t0 = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - t0


def bench_block_size(data, block_size, tmp):
    path = os.path.join(tmp, "s-%d.snpk" % block_size)
    info, t_comp = timed(lambda: sc.compress_snapshot(data, path, block_size, LEVEL))

    # sequential extract (all blocks, disk page cache hot)
    _data, t_extract = timed(lambda: sc.extract_snapshot(path))
    assert _data == data  # lossless check inside the benchmark too

    # full verification pass
    report, t_verify = timed(lambda: sc.verify_archive(path))
    assert report.ok

    rng = random.Random(7)
    cold_us, reuse_us, cached_us, cross_us = [], [], [], []

    def one_cold():
        ar = sc.Archive(path)
        try:
            start = rng.randrange(0, len(data) - 1024)
            out = ar.read_range(start, 128)
            assert out == data[start : start + 128]
        finally:
            ar.close()

    for _ in range(REPS // 4):
        _, dt = timed(one_cold)
        cold_us.append(dt * 1e6)

    ar = sc.Archive(path)
    try:
        ar.read_block(0)  # warm-up
        for _ in range(REPS):
            start = rng.randrange(0, len(data) - 1024)
            _, dt = timed(lambda start=start: ar.read_range(start, 128))
            reuse_us.append(dt * 1e6)
        # true block-cache hits: preload a block, then read inside it
        mid_block = (info.num_blocks // 2)
        ar.read_block(mid_block)
        mid_off = mid_block * block_size + block_size // 2
        for _ in range(REPS):
            _, dt = timed(lambda: ar.read_range(mid_off, 128))
            cached_us.append(dt * 1e6)
        # ranges spanning 2-3 blocks (8 KiB out of a smaller block / etc.)
        span = block_size * 2 + 17
        for _ in range(REPS // 2):
            start = rng.randrange(0, len(data) - span)
            _, dt = timed(lambda start=start: ar.read_range(start, span))
            cross_us.append(dt * 1e6)
    finally:
        ar.close()

    def p50_99(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2], xs[int(len(xs) * 0.99)]

    c50, c99 = p50_99(cold_us)
    u50, u99 = p50_99(reuse_us)
    h50, h99 = p50_99(cached_us)
    x50, x99 = p50_99(cross_us)
    return {
        "block_size": block_size,
        "blocks": info.num_blocks,
        "ratio": info.ratio(),
        "compressed_mib": info.compressed_size / 1048576,
        "compress_mbs": SIZE / t_comp / 1048576,
        "extract_mbs": SIZE / t_extract / 1048576,
        "verify_mbs": SIZE / t_verify / 1048576,
        "cold_p50_us": c50, "cold_p99_us": c99,
        "reuse_p50_us": u50, "reuse_p99_us": u99,
        "cached_p50_us": h50, "cached_p99_us": h99,
        "cross_p50_us": x50, "cross_p99_us": x99,
    }


def main():
    tmp = tempfile.mkdtemp(prefix="snapchunk-bench-")
    try:
        print("generating %d MiB mixed snapshot ..." % (SIZE // 1048576))
        data = make_snapshot(SIZE)
        print(
            "%-9s %7s %9s %9s %9s %9s | %-21s %-21s %-21s %-21s"
            % ("block", "blocks", "comp_MiB", "ratio", "cmpr_MB/s",
               "vrfy_MB/s", "open+read us p50/p99",
               "reuse-open us p50/p99", "cached-hit us p50/p99",
               "span~2blk us p50/p99")
        )
        rows = []
        for bs in (4 * 1024, 64 * 1024, 1024 * 1024):
            r = bench_block_size(data, bs, tmp)
            rows.append(r)
            print(
                "%-9d %7d %9.2f %9.4f %9.1f %9.1f | %8.1f / %-8.1f %8.1f / %-8.1f %8.1f / %-8.1f %8.1f / %.1f"
                % (r["block_size"], r["blocks"], r["compressed_mib"],
                   r["ratio"], r["compress_mbs"], r["verify_mbs"],
                   r["cold_p50_us"], r["cold_p99_us"],
                   r["reuse_p50_us"], r["reuse_p99_us"],
                   r["cached_p50_us"], r["cached_p99_us"],
                   r["cross_p50_us"], r["cross_p99_us"])
            )

        # contrast: full-extract cost to touch the same 128 bytes naively
        path = os.path.join(tmp, "s-%d.snpk" % (1024 * 1024))
        ar = sc.Archive(path)
        try:
            t0 = time.perf_counter()
            whole = ar.extract_all()
            _ = whole[10_000_000:10_000_128]
            naive_ms = (time.perf_counter() - t0) * 1000
            t0 = time.perf_counter()
            _ = ar.read_range(10_000_000, 128)
            indexed_ms = (time.perf_counter() - t0) * 1000
        finally:
            ar.close()
        print()
        print("read 128 B @10 MB offset with 1 MiB blocks:")
        print("  indexed block read: %.3f ms" % indexed_ms)
        print("  naive full extract: %.3f ms  (speedup %.0fx)"
              % (naive_ms, naive_ms / indexed_ms))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
