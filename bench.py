"""内存峰值与吞吐基准。运行：python3 bench.py"""

import json
import time
import tracemalloc

from stream_parser import StreamingParser, parse_all


def make_data(n_records: int) -> bytes:
    line = json.dumps(
        {"id": 0, "name": "user-0", "tags": ["a", "b", 0],
         "payload": "z" * 180, "score": 1.5}
    ).encode() + b"\n"
    return line * n_records


def measure(fn):
    tracemalloc.start()
    t0 = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, elapsed, peak


def main():
    n = 200_000
    data = make_data(n)
    size_mb = len(data) / 1e6
    print(f"input: {n} records, {size_mb:.1f} MB\n")
    print(f"{'mode':<28}{'peak mem':>12}{'time':>10}{'throughput':>14}")
    print("-" * 64)

    def oneshot():
        return len(parse_all(data))

    def streaming(chunk_size, keep):
        parser = StreamingParser()
        count = 0
        sink = [] if keep else None
        for i in range(0, len(data), chunk_size):
            for rec in parser.feed(data[i:i + chunk_size]):
                count += 1
                if keep:
                    sink.append(rec)
        parser.finish()
        return count

    for label, fn in [
        ("one-shot parse_all", oneshot),
        ("stream 4 KiB chunks", lambda: streaming(4 << 10, False)),
        ("stream 64 KiB chunks", lambda: streaming(64 << 10, False)),
        ("stream 1 MiB chunks", lambda: streaming(1 << 20, False)),
        ("stream 64 KiB, keep all", lambda: streaming(64 << 10, True)),
    ]:
        count, elapsed, peak = measure(fn)
        assert count == n, (label, count)
        print(f"{label:<28}{peak/1e6:>9.2f} MB{elapsed:>8.2f} s"
              f"{size_mb/elapsed:>10.1f} MB/s")


if __name__ == "__main__":
    main()
