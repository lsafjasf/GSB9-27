"""性能对比：构造混合日志（JSON 行 / 自由文本行 / 超长行），
统计过滤前后体积与过滤耗时。

运行：python3 bench.py [行数，默认 20000]
产物：artifacts/perf.json  artifacts/bench_input.log  artifacts/bench_output.log
"""
import json
import os
import random
import sys
import time

from redact import Redactor, redact_stream

ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")


def gen_lines(n, seed=42):
    rng = random.Random(seed)
    for i in range(n):
        kind = i % 10
        if kind < 6:  # JSON 结构行
            yield json.dumps({
                "ts": "2026-09-28T10:%02d:%02d+08:00" % (i % 60, i % 60),
                "level": "INFO", "svc": "svc-%d" % (i % 7),
                "user": {"id": i, "email": "user%d@example.com" % i},
                "password": "pw-%d-x9#s" % i if i % 3 == 0 else None,
                "msg": "op=%s latency=%dms" % (rng.choice(["read", "write", "auth"]), i % 997),
            }, ensure_ascii=False)
        elif kind < 9 or i % 100 != 9:  # 自由文本行
            yield ("2026-09-28 INFO request from 138%08d token=tok_%d_abcd "
                   "path=/api/v1/orders status=200" % (i % 10 ** 8, i))
        else:  # 超长行（约 1MB，每 100 行一条），秘密埋在中间
            pad = "x" * (512 * 1024)
            yield ("prefix " + pad + " password=buried_secret_%d " % i +
                   "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c "
                   + pad + " suffix")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    os.makedirs(ART, exist_ok=True)
    in_path = os.path.join(ART, "bench_input.log")
    out_path = os.path.join(ART, "bench_output.log")

    with open(in_path, "w", encoding="utf-8") as f:
        for line in gen_lines(n):
            f.write(line + "\n")
    bytes_in = os.path.getsize(in_path)

    redactor = Redactor()
    t0 = time.perf_counter()
    with open(in_path, encoding="utf-8") as inf, \
            open(out_path, "w", encoding="utf-8") as outf:
        stats = redact_stream(inf, outf, redactor)
    elapsed = time.perf_counter() - t0
    bytes_out = os.path.getsize(out_path)

    result = {
        "lines": stats["lines"],
        "json_lines": stats["json_lines"],
        "text_lines": stats["text_lines"],
        "long_lines_1mb": stats["lines"] // 100,
        "redactions": stats["redactions"],
        "size_before_bytes": bytes_in,
        "size_after_bytes": bytes_out,
        "size_delta_pct": round((bytes_out - bytes_in) * 100.0 / bytes_in, 2),
        "elapsed_sec": round(elapsed, 3),
        "throughput_mb_per_sec": round(bytes_in / 1024 / 1024 / elapsed, 1),
        "lines_per_sec": round(stats["lines"] / elapsed),
    }
    with open(os.path.join(ART, "perf.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
