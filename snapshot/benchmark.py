"""Compression-ratio and random-read latency benchmark.

Run:  python3 benchmark.py [size_mb] [chunk_kb]
Default: 16 MiB payload, 64 KiB chunks. Also runs a 1000+ chunk configuration.
"""

import os
import random
import sys
import tempfile
import time

import snapshotstore as ss

MIB = 1024 * 1024


def make_data(n_bytes: int, seed: int = 2026) -> bytes:
    """Mixed snapshot-like payload: highly compressible runs + random parts."""
    rnd = random.Random(seed)
    parts = []
    total = 0
    while total < n_bytes:
        if rnd.random() < 0.75:
            run = b"seq=%d id=%08d state=%s " % (
                rnd.getrandbits(20), rnd.getrandbits(32),
                b"RUNNING HALTED PENDING".split()[rnd.randrange(3)])
            run = run * rnd.randrange(2, 12)
        else:
            run = bytes(rnd.getrandbits(8) for _ in range(rnd.randrange(16, 256)))
        parts.append(run)
        total += len(run)
    return b"".join(parts)[:n_bytes]


def timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, (time.perf_counter() - t0) * 1000.0


def corrupt_one_byte(path, offset):
    with open(path, "r+b") as fh:
        fh.seek(offset)
        b = fh.read(1)
        fh.seek(offset)
        fh.write(bytes([b[0] ^ 0xFF]))


def run_case(data: bytes, chunk_size: int, label: str, tmpdir: str,
             n_reads: int = 5000) -> None:
    tag = chunk_size
    path = os.path.join(tmpdir, "snap_%d.bin" % tag)
    replica = os.path.join(tmpdir, "replica_%d.bin" % tag)

    stats, t_write = timed(ss.write_snapshot, path, data, chunk_size=chunk_size)
    ss.write_snapshot(replica, data, chunk_size=chunk_size)

    with ss.SnapshotReader(path) as rd:
        _, t_full = timed(rd.read_all)  # baseline: decompress everything

        rnd = random.Random(7)
        ranges = [(rnd.randrange(len(data)), rnd.randrange(1, 4097))
                  for _ in range(n_reads)]
        for start, length in ranges[:100]:  # warm page cache
            rd.read_range(start, length=min(length, len(data) - start))

        t0 = time.perf_counter()
        checked = 0
        for start, length in ranges:
            length = min(length, len(data) - start)
            got = rd.read_range(start, length=length)
            assert got == data[start:start + length]
            checked += 1
        t_random_ms = (time.perf_counter() - t0) * 1000.0

    ratio = stats["compressed_size"] / stats["original_size"]
    avg_us = t_random_ms / checked * 1000.0

    print("=" * 70)
    print(label)
    print("-" * 70)
    print("  original size        : %12d bytes (%7.2f MiB)"
          % (stats["original_size"], stats["original_size"] / MIB))
    print("  archive size         : %12d bytes (%7.2f MiB)"
          % (stats["compressed_size"], stats["compressed_size"] / MIB))
    print("  chunks               : %12d  (chunk size %d KiB)"
          % (stats["chunks"], chunk_size // 1024))
    print("  compression ratio    : %12.3f  (space saved %.1f%%)"
          % (ratio, (1.0 - ratio) * 100))
    print("  write + compress     : %12.2f ms" % t_write)
    print("  full decompress      : %12.2f ms  (baseline: extract all)"
          % t_full)
    print("  random reads         : %12d  (each asserted byte-identical)"
          % checked)
    print("  random read total    : %12.2f ms" % t_random_ms)
    print("  per random read      : %12.2f us avg" % avg_us)

    with ss.SnapshotReader(path) as good:
        victim = good.chunks[len(good.chunks) // 2].offset
    corrupt_one_byte(path, victim)
    with ss.SnapshotReader(path) as rd:
        bad, t_verify = timed(rd.verify)
    repaired, t_restore = timed(ss.restore_chunks, path, [replica],
                                auto_repair=True)
    with ss.SnapshotReader(path) as rd:
        assert rd.verify() == []
        assert rd.read_all() == data
    print("  corruption located   : chunk %s in %8.2f ms (full verify)"
          % (bad, t_verify))
    print("  replica repair       : chunk %s in %8.2f ms (atomic rewrite)"
          % (repaired, t_restore))


def main() -> None:
    size_mb = float(sys.argv[1]) if len(sys.argv) > 1 else 16.0
    chunk_kb = int(sys.argv[2]) if len(sys.argv) > 2 else 64

    with tempfile.TemporaryDirectory(prefix="snapbench_") as tmpdir:
        run_case(make_data(int(size_mb * MIB)), chunk_kb * 1024,
                 "Case A: %.0f MiB snapshot, %d KiB chunks"
                 % (size_mb, chunk_kb), tmpdir)
        run_case(make_data(4 * MIB), 2 * 1024,
                 "Case B: 4 MiB snapshot, 2 KiB chunks (1000+ chunks)",
                 tmpdir, n_reads=2000)
    print("=" * 70)
    print("All benchmark assertions passed.")


if __name__ == "__main__":
    main()
