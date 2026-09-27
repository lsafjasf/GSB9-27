"""Demo build pipeline wired with provenance recording.

Pipeline (all "tools" are stdlib Python functions with pinned versions):
  1. manifest  : scans src/            -> build/manifest.txt   (generated)
  2. normalize : src/*.src + config    -> build/norm/*.n       (shared input:
                 config.cfg is consumed by every normalize run)
  3. compile   : build/norm/*.n + build/manifest.txt
                 -> build/obj/*.o      (generated files re-consumed twice)
  4. link      : build/obj/*.o         -> dist/app.bin         (final artifact)

Run: python3 -m provbuild.build_demo <workdir> <trace.jsonl>
"""

import json
import os
import sys

from .provenance import ProvenanceRecorder

TOOL_VERSIONS = {
    "manifest": "1.0.0",
    "normalize": "1.2.0",
    "compile": "2.0.1",
    "link": "2.0.1",
}


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def run_pipeline(workdir, trace_path, source_names, final_artifact):
    """Execute the demo pipeline, recording every run. Returns the list
    of final artifacts."""
    src_dir = os.path.join(workdir, "src")
    build_dir = os.path.join(workdir, "build")
    recorder = ProvenanceRecorder(trace_path)

    config = os.path.join(src_dir, "config.cfg")
    sources = [os.path.join(src_dir, n) for n in source_names]

    # 1. manifest (generated, re-consumed by every compile run)
    manifest = os.path.join(build_dir, "manifest.txt")
    listing = "".join(sorted(os.listdir(src_dir))).encode()
    _write(manifest, listing)
    recorder.record("manifest", TOOL_VERSIONS["manifest"],
                    ["--src", src_dir], sources + [config], [manifest])

    objects = []
    for src in sources:
        name = os.path.splitext(os.path.basename(src))[0]
        norm = os.path.join(build_dir, "norm", name + ".n")
        obj = os.path.join(build_dir, "obj", name + ".o")

        # 2. normalize: consumes the shared config.cfg
        _write(norm, _read(src).upper() + b"|" + _read(config))
        recorder.record("normalize", TOOL_VERSIONS["normalize"],
                        ["--cfg", config, "-o", norm, src],
                        [src, config], [norm])

        # 3. compile: consumes generated norm + generated manifest
        _write(obj, b"OBJ(" + _read(norm) + b")#" + _read(manifest))
        recorder.record("compile", TOOL_VERSIONS["compile"],
                        ["-o", obj, norm, manifest],
                        [norm, manifest], [obj])
        objects.append(obj)

    # 4. link
    _write(final_artifact, b"BIN[" + b"][".join(_read(o) for o in objects) + b"]")
    recorder.record("link", TOOL_VERSIONS["link"],
                    ["-o", final_artifact] + objects, objects, [final_artifact])
    return [final_artifact]


def main(argv):
    workdir = argv[1] if len(argv) > 1 else "demo_out"
    trace_path = argv[2] if len(argv) > 2 else os.path.join(workdir, "trace.jsonl")
    src_dir = os.path.join(workdir, "src")
    os.makedirs(src_dir, exist_ok=True)
    _write(os.path.join(src_dir, "config.cfg"), b"opt=2\ndebug=false\n")
    names = []
    for i in range(5):
        name = f"mod{i}.src"
        _write(os.path.join(src_dir, name), f"module {i}\n".encode())
        names.append(name)
    final = os.path.join(workdir, "dist", "app.bin")
    artifacts = run_pipeline(workdir, trace_path, names, final)

    from .provenance import load_trace, ProvenanceGraph
    records = load_trace(trace_path)
    errors = ProvenanceGraph(records).validate(artifacts)
    print(json.dumps({
        "workdir": workdir,
        "trace": trace_path,
        "runs": len(records),
        "final_artifacts": artifacts,
        "validation_errors": errors,
    }, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
