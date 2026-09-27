"""rotlog 吞吐基准：单线程/多线程、压缩开关下的写入吞吐。"""

import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rotlog import RotatingLogWriter

LINES = 300_000
PAYLOAD = "x" * 100  # 每行约 112 字节


def bench(label, threads=1, **kw):
    d = tempfile.mkdtemp(prefix="rotlog-bench-")
    path = os.path.join(d, "bench.log")
    w = RotatingLogWriter(path, max_bytes=8 * 1024 * 1024,
                          max_backups=100, **kw)
    per = LINES // threads

    def worker(tid):
        for i in range(per):
            w.write_line(f"t{tid}-{i:08d}-{PAYLOAD}")

    t0 = time.perf_counter()
    ts = [threading.Thread(target=worker, args=(t,)) for t in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    w.close()
    dt = time.perf_counter() - t0
    total = per * threads
    mb = w.stats.bytes_written / 1e6
    print(f"{label:<38} {total / dt / 1e3:>9.0f} K行/s  "
          f"{mb / dt:>8.1f} MB/s  轮转 {w.stats.rotations} 次  "
          f"错误 {w.stats.rotate_errors}")
    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    print(f"Python {sys.version.split()[0]}, 每轮 {LINES} 行 x ~112B, "
          f"max_bytes=8MiB")
    bench("单线程, 不压缩, 缓冲写", compress=False)
    bench("单线程, gzip压缩, 缓冲写", compress=True)
    bench("单线程, 不压缩, 每次flush(sync)", compress=False, sync=True)
    bench("8线程, 不压缩, 缓冲写", threads=8, compress=False)
    bench("8线程, gzip压缩, 缓冲写", threads=8, compress=True)
