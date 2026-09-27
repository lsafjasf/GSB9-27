"""Self-tests for the provbuild provenance library (stdlib unittest)."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from provbuild.provenance import (
    ProvenanceError,
    ProvenanceGraph,
    ReplayEntry,
    Tool,
    fingerprint_file,
)
from provbuild.build_demo import CC, LD, run_build


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="provtest-")
        self.addCleanup(shutil.rmtree, self.root, True)


class TestRecording(Base):
    def test_records_full_input_set_with_tool_and_argv(self):
        graph, app = run_build(os.path.join(self.root, "b"), 3)
        link = graph.steps[-1]
        self.assertEqual(link.tool.name, "ld")
        self.assertEqual(link.tool.version, "2.41")
        self.assertEqual(link.tool.argv, ("--gc-sections",))
        compile_step = graph.steps[0]
        self.assertEqual(compile_step.tool.argv, ("-O2", "-c"))
        # inputs carry content fingerprints, not timestamps
        for path, fp in compile_step.inputs:
            self.assertEqual(fp, fingerprint_file(path))
            self.assertEqual(len(fp), 64)

    def test_fingerprint_ignores_mtime(self):
        graph, app = run_build(os.path.join(self.root, "b"), 2)
        src = os.path.join(self.root, "b", "src", "mod_0.c")
        before = graph.check_replay(app)
        self.assertTrue(before.reproducible)
        os.utime(src, (1_700_000_000, 1_700_000_000))  # touch mtime only
        after = graph.check_replay(app)
        self.assertTrue(after.reproducible, "mtime-only change must not invalidate replay")

    def test_unknown_input_rejected(self):
        graph = ProvenanceGraph()
        with self.assertRaises(ProvenanceError):
            graph.record_step("s1", CC, inputs=["/nonexistent/x.c"], outputs=["/tmp/o"])

    def test_double_production_rejected(self):
        graph, _ = run_build(os.path.join(self.root, "b"), 1)
        obj = os.path.join(self.root, "b", "build", "mod_0.o")
        with self.assertRaises(ProvenanceError):
            graph.record_step("dup", CC, inputs=[], outputs=[obj])


class TestSharedAndReconsumedInputs(Base):
    def test_shared_source_traced_from_multiple_artifacts(self):
        graph, app = run_build(os.path.join(self.root, "b"), 4)
        header = os.path.join(self.root, "b", "include", "common.h")
        objs = [os.path.join(self.root, "b", "build", f"mod_{i}.o") for i in range(4)]
        for obj in objs:
            self.assertIn(header, graph.upstream(obj))
        self.assertIn(header, graph.upstream(app))
        # mutate the shared header -> every dependent artifact goes stale
        with open(header, "a", encoding="utf-8") as fh:
            fh.write("#define EXTRA 1\n")
        for obj in objs + [app]:
            report = graph.check_replay(obj)
            self.assertFalse(report.reproducible)
            self.assertIn(header, report.changed())

    def test_generated_file_reconsumed_downstream(self):
        graph, app = run_build(os.path.join(self.root, "b"), 3)
        obj0 = os.path.join(self.root, "b", "build", "mod_0.o")
        src0 = os.path.join(self.root, "b", "src", "mod_0.c")
        ups = graph.upstream(app)
        # the app traces through generated .o intermediates back to sources
        self.assertIn(obj0, ups)
        self.assertIn(src0, ups)
        # change a leaf source: replay of the final artifact pinpoints it
        with open(src0, "a", encoding="utf-8") as fh:
            fh.write("int extra(void);\n")
        report = graph.check_replay(app)
        self.assertEqual(report.changed(), [src0])
        self.assertEqual(report.missing(), [])

    def test_missing_input_detected(self):
        graph, app = run_build(os.path.join(self.root, "b"), 2)
        src1 = os.path.join(self.root, "b", "src", "mod_1.c")
        os.remove(src1)
        report = graph.check_replay(app)
        self.assertFalse(report.reproducible)
        self.assertEqual(report.missing(), [src1])
        entry = next(e for e in report.entries if e.path == src1)
        self.assertEqual(entry.status, ReplayEntry.MISSING)
        self.assertIsNone(entry.current_fingerprint)


class TestGraphValidation(Base):
    def test_valid_build_has_no_violations(self):
        graph, _ = run_build(os.path.join(self.root, "b"), 5)
        self.assertEqual(graph.validate(), [])

    def test_orphan_source_detected(self):
        graph, _ = run_build(os.path.join(self.root, "b"), 2)
        stray = os.path.join(self.root, "b", "src", "stray.c")
        with open(stray, "w") as fh:
            fh.write("int stray;\n")
        graph.add_source(stray)
        errors = graph.validate()
        self.assertTrue(any("orphan source" in e and "stray.c" in e for e in errors), errors)

    def test_orphan_artifact_detected(self):
        graph, _ = run_build(os.path.join(self.root, "b"), 2)
        dead = os.path.join(self.root, "b", "build", "dead.o")
        with open(dead, "w") as fh:
            fh.write("dead\n")
        src0 = os.path.join(self.root, "b", "src", "mod_0.c")
        graph.record_step("dead-step", CC, inputs=[src0], outputs=[dead])
        errors = graph.validate()
        self.assertTrue(any("orphan artifact" in e and "dead.o" in e for e in errors), errors)

    def test_dangling_input_detected(self):
        graph, _ = run_build(os.path.join(self.root, "b"), 1)
        graph.steps[0].inputs.append(("/ghost/input.c", "0" * 64))
        errors = graph.validate()
        self.assertTrue(any("dangling input" in e for e in errors), errors)

    def test_cycle_detected(self):
        graph, _ = run_build(os.path.join(self.root, "b"), 2)
        # forge a cycle: make mod_0.o an input of the step producing mod_0.o
        obj0 = os.path.join(self.root, "b", "build", "mod_0.o")
        graph.steps[0].inputs.append((obj0, "0" * 64))
        errors = graph.validate()
        self.assertTrue(any("cycle" in e for e in errors), errors)

    def test_every_artifact_traces_to_sources(self):
        graph, app = run_build(os.path.join(self.root, "b"), 6)
        for artifact in graph.artifacts():
            ups = graph.upstream(artifact)
            self.assertTrue(ups, artifact)
            self.assertTrue(any(p in graph.sources for p in ups), artifact)


class TestReplayAndSerialization(Base):
    def test_clean_build_is_reproducible(self):
        graph, app = run_build(os.path.join(self.root, "b"), 3)
        report = graph.check_replay(app)
        self.assertTrue(report.reproducible)
        self.assertIn("REPRODUCIBLE", report.summary())

    def test_roundtrip_json(self):
        graph, app = run_build(os.path.join(self.root, "b"), 3)
        prov = os.path.join(self.root, "prov.json")
        graph.save(prov)
        loaded = ProvenanceGraph.load(prov)
        self.assertEqual(loaded.validate(), [])
        self.assertEqual(loaded.to_dict(), graph.to_dict())
        self.assertTrue(loaded.check_replay(app).reproducible)

    def test_replay_from_serialized_record_only(self):
        """Given only the artifact path + saved provenance record, decide change."""
        graph, app = run_build(os.path.join(self.root, "b"), 3)
        prov = os.path.join(self.root, "prov.json")
        graph.save(prov)
        del graph
        loaded = ProvenanceGraph.load(prov)
        self.assertTrue(loaded.check_replay(app).reproducible)
        src = os.path.join(self.root, "b", "src", "mod_2.c")
        with open(src, "a") as fh:
            fh.write("// tampered\n")
        report = loaded.check_replay(app)
        self.assertFalse(report.reproducible)
        self.assertEqual(report.changed(), [src])

    def test_unknown_artifact_raises(self):
        graph, _ = run_build(os.path.join(self.root, "b"), 1)
        with self.assertRaises(ProvenanceError):
            graph.upstream("/nope")
        with self.assertRaises(ProvenanceError):
            graph.check_replay("/nope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
