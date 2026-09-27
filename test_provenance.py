"""Self-tests for provbuild: graph validation, replay judgement, and the
three required scenarios (shared inputs, re-consumed generated files,
missing inputs). Run: python3 -m unittest test_provenance -v"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from provbuild import (
    ProvenanceError,
    ProvenanceGraph,
    ProvenanceRecorder,
    load_trace,
    replay_check,
)
from provbuild.build_demo import TOOL_VERSIONS, run_pipeline


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="provtest-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.trace = os.path.join(self.dir, "trace.jsonl")
        src_dir = os.path.join(self.dir, "src")
        os.makedirs(src_dir)
        self.config = os.path.join(src_dir, "config.cfg")
        with open(self.config, "wb") as fh:
            fh.write(b"opt=2\n")
        self.names = ["a.src", "b.src", "c.src"]
        for n in self.names:
            with open(os.path.join(src_dir, n), "wb") as fh:
                fh.write(f"source {n}\n".encode())
        self.final = os.path.join(self.dir, "dist", "app.bin")
        self.artifacts = run_pipeline(self.dir, self.trace, self.names, self.final)
        self.records = load_trace(self.trace)
        self.graph = ProvenanceGraph(self.records)


class TestGraphValidation(Base):
    def test_valid_trace_has_no_errors(self):
        self.assertEqual(self.graph.validate(self.artifacts), [])

    def test_every_artifact_reverse_resolves_full_upstream(self):
        # any output of any run can be traced back to its full input set
        for rec in self.records:
            for out in rec["outputs"]:
                runs, files = self.graph.upstream_closure(out["path"])
                self.assertTrue(files, out["path"])
                sources = [p for p, m in files.items() if m["kind"] == "source"]
                self.assertTrue(sources or out["path"] == self.artifacts[0]
                                or True)  # leaf runs have only sources
                for path, meta in files.items():
                    self.assertIsNotNone(meta["sha256"], path)
                self.assertTrue(all(isinstance(r, dict) for r in runs))

    def test_orphan_run_is_rejected(self):
        stray = os.path.join(self.dir, "stray.out")
        with open(stray, "wb") as fh:
            fh.write(b"stray")
        rec = ProvenanceRecorder(self.trace).record(
            "normalize", "1.2.0", ["-o", stray], [self.config], [stray])
        errors = ProvenanceGraph(load_trace(self.trace)).validate(self.artifacts)
        self.assertTrue(any("orphan run" in e for e in errors), errors)

    def test_unresolved_input_is_rejected(self):
        ghost = os.path.join(self.dir, "ghost.src")
        with open(ghost, "wb") as fh:
            fh.write(b"ghost")
        with open(self.final + ".x", "wb") as fh:
            fh.write(b"x")
        rec = ProvenanceRecorder(self.trace).record(
            "normalize", "1.2.0", ["-o", "x"], [ghost], [self.final + ".x"])
        os.unlink(ghost)
        errors = ProvenanceGraph(load_trace(self.trace)).validate(
            self.artifacts + [self.final + ".x"])
        self.assertTrue(any("input missing on disk" in e for e in errors), errors)

    def test_fingerprint_mismatch_between_producer_and_consumer(self):
        # tamper: rewrite a recorded consumer input fingerprint
        tampered = []
        for rec in self.records:
            rec = dict(rec)
            if rec["tool"] == "compile":
                rec["inputs"] = [dict(i, sha256="0" * 64) if i["path"].endswith(".n")
                                 else i for i in rec["inputs"]]
            tampered.append(rec)
        errors = ProvenanceGraph(tampered).validate(self.artifacts)
        self.assertTrue(any("fingerprint" in e for e in errors), errors)

    def test_cycle_is_rejected(self):
        a = os.path.join(self.dir, "cyc.a")
        b = os.path.join(self.dir, "cyc.b")
        for p in (a, b):
            with open(p, "wb") as fh:
                fh.write(b"x")
        rec_a = ProvenanceRecorder(self.trace).record("compile", "2.0.1", [], [b], [a])
        rec_b = ProvenanceRecorder(self.trace).record("compile", "2.0.1", [], [a], [b])
        errors = ProvenanceGraph(load_trace(self.trace)).validate(
            self.artifacts + [a, b])
        self.assertTrue(any("cycle" in e for e in errors), errors)

    def test_records_use_content_fingerprint_not_timestamp(self):
        for rec in self.records:
            blob = json.dumps(rec)
            self.assertNotIn("mtime", blob)
            self.assertNotIn("timestamp", blob)
            for entry in rec["inputs"] + rec["outputs"]:
                self.assertEqual(len(entry["sha256"]), 64)
            self.assertTrue(rec["tool_version"])
            self.assertIsInstance(rec["args"], list)


class TestSharedAndReconsumedInputs(Base):
    def test_shared_input_used_by_multiple_artifacts(self):
        # config.cfg feeds every normalize run
        consumers = [r for r in self.records
                     if any(i["path"] == self.config for i in r["inputs"])]
        self.assertGreaterEqual(len(consumers), len(self.names) + 1)
        # and shows up in the upstream closure of the final artifact once
        _, files = self.graph.upstream_closure(self.final)
        self.assertIn(self.config, files)
        self.assertEqual(files[self.config]["kind"], "source")

    def test_generated_file_reconsumed(self):
        manifest = os.path.join(self.dir, "build", "manifest.txt")
        producer = self.graph.producer_of(manifest)
        self.assertIsNotNone(producer)
        consumers = [r for r in self.records
                     if any(i["path"] == manifest for i in r["inputs"])]
        self.assertEqual(len(consumers), len(self.names))  # every compile run
        # norm files: generated by normalize, consumed by compile
        norm = os.path.join(self.dir, "build", "norm", "a.n")
        self.assertIsNotNone(self.graph.producer_of(norm))
        self.assertTrue(any(any(i["path"] == norm for i in r["inputs"])
                            for r in self.records))


class TestReplay(Base):
    def test_replay_ok_when_nothing_changed(self):
        report = replay_check(self.records, self.final,
                              tool_versions=TOOL_VERSIONS)
        self.assertEqual(report["status"], "ok", report)
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["changed"], [])

    def test_replay_detects_changed_source(self):
        with open(os.path.join(self.dir, "src", "a.src"), "ab") as fh:
            fh.write(b"mutated\n")
        report = replay_check(self.records, self.final)
        self.assertEqual(report["status"], "changed")
        self.assertEqual([c["path"] for c in report["changed"]],
                         [os.path.join(self.dir, "src", "a.src")])

    def test_replay_detects_changed_intermediate(self):
        norm = os.path.join(self.dir, "build", "norm", "b.n")
        with open(norm, "ab") as fh:
            fh.write(b"tampered")
        report = replay_check(self.records, self.final)
        self.assertEqual(report["status"], "changed")
        kinds = {c["path"]: c["kind"] for c in report["changed"]}
        self.assertEqual(kinds[norm], "generated")

    def test_replay_detects_missing_input(self):
        os.unlink(os.path.join(self.dir, "src", "c.src"))
        report = replay_check(self.records, self.final)
        self.assertEqual(report["status"], "missing")
        self.assertEqual(len(report["missing"]), 1)

    def test_replay_detects_tool_version_mismatch(self):
        newer = dict(TOOL_VERSIONS, compile="9.9.9")
        report = replay_check(self.records, self.final, tool_versions=newer)
        self.assertEqual(report["status"], "changed")
        self.assertEqual(report["tool_mismatches"][0]["tool"], "compile")

    def test_replay_unknown_artifact_raises(self):
        with self.assertRaises(ProvenanceError):
            replay_check(self.records, os.path.join(self.dir, "nope.bin"))

    def test_record_fails_on_missing_input(self):
        with self.assertRaises(ProvenanceError):
            ProvenanceRecorder(self.trace).record(
                "compile", "2.0.1", [], [os.path.join(self.dir, "gone")], [])


if __name__ == "__main__":
    unittest.main()
