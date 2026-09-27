"""吞吐与内存基准：python3 bench_sse_stream.py [事件数]"""

import gc
import sys
import time
import tracemalloc

from sse_stream import SSEParser


def build_stream(n: int) -> bytes:
    parts = []
    for i in range(n):
        parts.append(f"id: {i}\nevent: bench\ndata: payload-{i}-{'x' * 64}\n\n".encode())
        if i % 16 == 0:
            parts.append(b": heartbeat\n")  # 混入心跳
    return b"".join(parts)


def run(n: int, chunk_size: int = 65536):
    stream = build_stream(n)
    mb = len(stream) / 1024 / 1024
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    parser = SSEParser()
    count = 0
    for i in range(0, len(stream), chunk_size):
        count += len(parser.feed(stream[i : i + chunk_size]))
    count += len(parser.close())
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert count == n, (count, n)
    print(f"events        : {n:,}")
    print(f"stream size   : {mb:.2f} MiB ({len(stream):,} bytes)")
    print(f"chunk size    : {chunk_size:,} bytes")
    print(f"elapsed       : {elapsed:.3f} s")
    print(f"throughput    : {n / elapsed:,.0f} events/s, {mb / elapsed:.1f} MiB/s")
    print(f"peak memory   : {peak / 1024:.1f} KiB (tracemalloc, 解析器侧增量分配)")
    # 稳态内存：持续喂入时缓冲不应随流长增长
    parser2 = SSEParser()
    for i in range(0, len(stream), chunk_size):
        parser2.feed(stream[i : i + chunk_size])
    parser2.close()
    print(f"residual buf  : {len(parser2._buf)} bytes (解析完 {n:,} 事件后内部残留)")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000
    run(n)
