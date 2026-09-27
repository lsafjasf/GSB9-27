"""Benchmark: provenance cost for 1000+ artifacts.

Builds N artifacts through a 2-step pipeline (normalize -> compile) with
one shared config consumed by every run, plus one final link step, then
measures:
  - wall time to record provenance (per run and total)
  - trace file size (total and per artifact)
  - graph validation time
  - replay-check time for one artifact

Run: python3 bench_provenance.py [N] [workdir]
"""

import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from provbuild import ProvenanceGraph, ProvenanceRecorder, load_trace, replay_check


def bench(n_artifacts, workdir):
    src_dir = os.path.join(workdir, "src")
    gen_dir = os.path.join(workdir, "gen")
    out_dir = os.path.join(workdir, "out")
    for d in (src_dir, gen_dir, out_dir):
        os.makedirs(d, exist_ok=True)
    trace_path = os.path.join(workdir, "trace.jsonl")

    config = os.path.join(src_dir, "config.cfg")
    with open(config, "wb") as fh:
        fh.write(b"opt=2\n")
    sources = []
    for i in range(n_artifacts):
        p = os.path.join(src_dir, f"mod{i}.src")
        with open(p, "wb") as fh:
            fh.write(f"module {i}\n".encode() * 4)
        sources.append(p)

    recorder = ProvenanceRecorder(trace_path)
    record_time = 0.0
    artifacts = []

    def timed_record(*args):
        nonlocal record_time
        t0 = time.perf_counter()
        recorder.record(*args)
        record_time += time.perf_counter() - t0

    t_build0 = time.perf_counter()
    intermediates = []
    for i, src in enumerate(sources):
        norm = os.path.join(gen_dir, f"mod{i}.n")
        with open(norm, "wb") as fh:
            fh.write(open(src, "rb").read().upper())
        timed_record("normalize", "1.2.0", ["--cfg", config, "-o", norm, src],
                     [src, config], [norm])

        art = os.path.join(out_dir, f"mod{i}.o")
        with open(art, "wb") as fh:
            fh.write(b"OBJ(" + open(norm, "rb").read() + b")")
        timed_record("compile", "2.0.1", ["-o", art, norm], [norm], [art])
        intermediates.append(norm)
        artifacts.append(art)

    final = os.path.join(out_dir, "app.bin")
    with open(final, "wb") as fh:
        for a in artifacts:
            fh.write(open(a, "rb").read())
    timed_record("link", "2.0.1", ["-o", final] + artifacts, artifacts, [final])
    artifacts.append(final)
    build_time = time.perf_counter() - t_build0

    trace_size = os.path.getsize(trace_path)

    t0 = time.perf_counter()
    records = load_trace(trace_path)
    load_time = time.perf_counter() - t0

    t0 = time.perf_counter()
    errors = ProvenanceGraph(records).validate([final])
    validate_time = time.perf_counter() - t0
    assert not errors, errors

    t0 = time.perf_counter()
    report = replay_check(records, final)
    replay_time = time.perf_counter() - t0
    assert report["status"] == "ok", report

    run_count = len(records)
    return {
        "artifacts": n_artifacts + 1,
        "runs_recorded": run_count,
        "build_wall_time_s": round(build_time, 3),
        "record_time_total_s": round(record_time, 3),
        "record_time_per_run_ms": round(record_time / run_count * 1000, 3),
        "record_overhead_pct_of_build": round(record_time / build_time * 100, 1),
        "trace_size_bytes": trace_size,
        "trace_size_per_run_bytes": round(trace_size / run_count, 1),
        "trace_load_time_s": round(load_time, 3),
        "graph_validate_time_s": round(validate_time, 3),
        "replay_check_time_s": round(replay_time, 3),
        "replay_upstream_inputs": report["input_count"],
    }


def main(argv):
    n = int(argv[1]) if len(argv) > 1 else 1200
    workdir = argv[2] if len(argv) > 2 else tempfile.mkdtemp(prefix="provbench-")
    cleanup = len(argv) <= 2
    try:
        result = bench(n, workdir)
        print(json.dumps(result, indent=2))
    finally:
        if cleanup:
            shutil.rmtree(workdir, True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
