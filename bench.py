"""Benchmark: dedup space savings and GC timing for cas.ContentStore."""

import os
import random
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cas import ContentStore


def fmt_bytes(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def scenario_dedup():
    """Many logical writes, few unique contents -> space saving."""
    print("== Scenario A: dedup space savings ==")
    unique_contents = [os.urandom(256) for _ in range(500)]
    n_puts = 100_000
    rng = random.Random(42)
    with tempfile.TemporaryDirectory() as tmp:
        store = ContentStore(tmp)
        t0 = time.perf_counter()
        logical = 0
        for _ in range(n_puts):
            data = rng.choice(unique_contents)
            store.put(data)
            logical += len(data)
        t_put = time.perf_counter() - t0
        stats = store.stats()
        saved = logical - stats["disk_bytes"]
        print(f"  logical writes      : {n_puts:,} puts, {fmt_bytes(logical)}")
        print(f"  unique objects      : {stats['objects']:,}")
        print(f"  disk usage (blobs+index): {fmt_bytes(stats['disk_bytes'])}")
        print(f"  space saved         : {fmt_bytes(saved)} "
              f"({saved / logical * 100:.2f}%)")
        print(f"  put throughput      : {n_puts / t_put:,.0f} puts/s "
              f"({t_put:.2f}s total)")
        t0 = time.perf_counter()
        rep = store.sweep()
        print(f"  mark-sweep (nothing to collect): "
              f"{rep['elapsed_s'] * 1e3:.1f} ms over {rep['scanned']:,} objects")
        store.close()


def scenario_many_small():
    """Many small unique objects -> GC timing."""
    print("== Scenario B: many small objects, GC timing ==")
    n = 50_000
    with tempfile.TemporaryDirectory() as tmp:
        store = ContentStore(tmp)
        t0 = time.perf_counter()
        hashes = store.put_many(f"object-{i}".encode() for i in range(n))
        t_put = time.perf_counter() - t0
        print(f"  put_many {n:,} unique small objects: {t_put:.2f}s "
              f"({n / t_put:,.0f} objs/s)")

        # refcount GC: pin all, then release half individually
        handles = [store.ref(h) for h in hashes]
        t0 = time.perf_counter()
        for handle in handles[: n // 2]:
            store.release(handle)
        t_refcount_gc = time.perf_counter() - t0
        print(f"  refcount GC of {n // 2:,} objects: "
              f"{t_refcount_gc:.2f}s "
              f"({n // 2 / t_refcount_gc:,.0f} objs/s)")

        # simulate refcount drift: leaked +1 on the remaining unpinned
        # objects, so refcount GC can never reclaim them -> sweep's job
        for h in hashes[n // 2 :]:
            store._db.execute(
                "UPDATE objects SET refcount=refcount+1 WHERE hash=?", (h,)
            )
        store._db.commit()
        for handle in handles[n // 2 :]:
            store.release(handle)  # refcount now 1 (leaked), unreachable
        assert store.stats()["objects"] == n // 2

        t0 = time.perf_counter()
        rep = store.sweep()
        t_sweep = time.perf_counter() - t0
        print(f"  mark-sweep over {rep['scanned']:,} objects: "
              f"{t_sweep:.2f}s, swept {rep['swept']:,} "
              f"({fmt_bytes(rep['bytes_reclaimed'])}), "
              f"drift corrections: {len(rep['refcount_corrections'])}")
        t0 = time.perf_counter()
        v = store.verify()
        t_verify = time.perf_counter() - t0
        print(f"  verify (hash check) : {t_verify:.2f}s, ok={v['ok']}, "
              f"objects={v['objects']:,}")
        store.compact()
        stats = store.stats()
        print(f"  final: {stats['objects']:,} objects, "
              f"{fmt_bytes(stats['disk_bytes'])} on disk")
        store.close()


def scenario_dedup_files():
    """Realistic: versioned file snapshots with small changes."""
    print("== Scenario C: 200 versions of a 1 MiB file, 1% changed ==")
    rng = random.Random(7)
    chunk_size = 4096
    base = [os.urandom(chunk_size) for _ in range(256)]  # 1 MiB of chunks
    logical = 0
    with tempfile.TemporaryDirectory() as tmp:
        store = ContentStore(tmp)
        t0 = time.perf_counter()
        for version in range(200):
            if version:
                for _ in range(3):  # mutate ~1% of chunks
                    base[rng.randrange(len(base))] = os.urandom(chunk_size)
            refs = store.put_many(base)
            manifest = b"\n".join(h.encode() for h in refs)
            store.put_root(f"v{version}", manifest, refs=refs)
            logical += sum(len(c) for c in base) + len(manifest)
        t_put = time.perf_counter() - t0
        stats = store.stats()
        saved = logical - stats["disk_bytes"]
        print(f"  logical size : {fmt_bytes(logical)} (200 versions)")
        print(f"  disk usage   : {fmt_bytes(stats['disk_bytes'])} "
              f"({stats['objects']:,} objects)")
        print(f"  space saved  : {fmt_bytes(saved)} "
              f"({saved / logical * 100:.2f}%)")
        print(f"  ingest time  : {t_put:.2f}s")
        # drop half the versions: refcount cascade reclaims orphaned chunks
        t0 = time.perf_counter()
        for version in range(0, 200, 2):
            store.remove_root(f"v{version}")
        t_gc = time.perf_counter() - t0
        after = store.stats()
        print(f"  refcount GC after dropping 100 versions: {t_gc:.2f}s, "
              f"{after['objects']:,} objects left, "
              f"{fmt_bytes(after['disk_bytes'])} on disk")
        # inject drift (leaked +1 on every object), then fallback sweep
        store._db.execute("UPDATE objects SET refcount=refcount+1")
        store._db.commit()
        t0 = time.perf_counter()
        rep = store.sweep()
        print(f"  mark-sweep with injected drift: {rep['elapsed_s']:.2f}s, "
              f"swept {rep['swept']:,}, "
              f"corrections {len(rep['refcount_corrections']):,}")
        print(f"  verify ok    : {store.verify()['ok']}")
        store.close()


if __name__ == "__main__":
    scenario_dedup()
    print()
    scenario_many_small()
    print()
    scenario_dedup_files()
