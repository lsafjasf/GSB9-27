"""Benchmark: provenance overhead at thousands-of-artifacts scale.

Measures, per scale:
  - record time   : provenance recording during the build (hashing + graph ops)
  - validate time : full graph self-consistency assertions
  - replay time   : content re-hash replay check of the final artifact
                    (its upstream closure covers every node in the graph)
  - store size    : serialized provenance JSON bytes (and bytes/artifact)
"""

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from provbuild.build_demo import run_build
from provbuild.provenance import ProvenanceGraph


def bench_scale(n_modules: int) -> dict:
    root = tempfile.mkdtemp(prefix="provbench-")
    try:
        t0 = time.perf_counter()
        run_build(root, n_modules, record=False)  # baseline: build only
        t_build = time.perf_counter() - t0

        shutil.rmtree(root)
        os.makedirs(root)

        t0 = time.perf_counter()
        graph, app = run_build(root, n_modules)   # build + provenance recording
        t_record = time.perf_counter() - t0

        n_artifacts = len(graph.artifacts())
        n_sources = len(graph.sources)
        n_steps = len(graph.steps)

        t0 = time.perf_counter()
        errors = graph.validate()
        t_validate = time.perf_counter() - t0
        assert not errors, errors

        prov_path = os.path.join(root, "provenance.json")
        t0 = time.perf_counter()
        size = graph.save(prov_path)
        t_save = time.perf_counter() - t0

        t0 = time.perf_counter()
        loaded = ProvenanceGraph.load(prov_path)
        t_load = time.perf_counter() - t0

        t0 = time.perf_counter()
        report = loaded.check_replay(app)  # walks + re-hashes full upstream closure
        t_replay = time.perf_counter() - t0
        assert report.reproducible

        return {
            "modules": n_modules,
            "artifacts": n_artifacts,
            "sources": n_sources,
            "steps": n_steps,
            "build_s": t_build,
            "record_s": t_record,
            "prov_overhead_s": t_record - t_build,
            "validate_s": t_validate,
            "save_s": t_save,
            "load_s": t_load,
            "replay_s": t_replay,
            "replay_inputs": len(report.entries),
            "store_bytes": size,
            "bytes_per_artifact": size / n_artifacts,
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    scales = [int(x) for x in sys.argv[1:]] or [1000, 2000, 5000]
    rows = [bench_scale(n) for n in scales]

    hdr = (
        f"{'artifacts':>9} {'sources':>8} {'steps':>7} "
        f"{'build(s)':>9} {'+prov(s)':>9} {'prov_ovh':>9} "
        f"{'validate(s)':>12} {'replay(s)':>10} "
        f"{'store(KiB)':>11} {'B/artifact':>10}"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['artifacts']:>9} {r['sources']:>8} {r['steps']:>7} "
            f"{r['build_s']:>9.3f} {r['record_s']:>9.3f} {r['prov_overhead_s']:>9.3f} "
            f"{r['validate_s']:>12.3f} {r['replay_s']:>10.3f} "
            f"{r['store_bytes']/1024:>11.1f} {r['bytes_per_artifact']:>10.1f}"
        )
    for r in rows:
        print(
            f"note: {r['artifacts']} artifacts -> replay re-hashed "
            f"{r['replay_inputs']} upstream inputs in {r['replay_s']*1000:.1f} ms"
        )


if __name__ == "__main__":
    main()
