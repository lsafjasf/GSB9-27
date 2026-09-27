"""Self-tests for tracelib: python3 -m unittest test_tracelib -v"""

import dataclasses
import json
import os
import re
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor

from tracelib import (
    HEADER_NAME,
    IntegrityValidator,
    MalformedContextError,
    SpanRecord,
    TamperedContextError,
    TraceContext,
)

SECRET = b"test-secret"
OTHER_SECRET = b"other-secret"

WIRE_RE = re.compile(r"^v1\.[0-9a-f]{32}\.[0-9a-f]{16}\.([0-9a-f]{16}|-)\.[01]\.[0-9a-f]{32}$")


def tamper(raw: str, index: int, value: str) -> str:
    parts = raw.split(".")
    parts[index] = value
    return ".".join(parts)


class TestEntryWithoutContext(unittest.TestCase):
    def test_extract_from_empty_carrier_returns_none(self):
        self.assertIsNone(TraceContext.extract({}, SECRET))

    def test_root_starts_new_trace(self):
        ctx = TraceContext.root(SECRET, sampled=True)
        self.assertIsNone(ctx.parent_id)
        self.assertTrue(ctx.sampled)
        self.assertRegex(ctx.serialize(), WIRE_RE)

    def test_root_default_makes_a_sampling_decision(self):
        ctx = TraceContext.root(SECRET)
        self.assertIsInstance(ctx.sampled, bool)


class TestSerialization(unittest.TestCase):
    def test_deterministic(self):
        ctx = TraceContext.root(SECRET, sampled=True).child()
        self.assertEqual(ctx.serialize(), ctx.serialize())

    def test_roundtrip_preserves_all_fields(self):
        ctx = TraceContext.root(SECRET, sampled=False).child().child()
        back = TraceContext.deserialize(ctx.serialize(), SECRET)
        self.assertEqual((ctx.trace_id, ctx.span_id, ctx.parent_id, ctx.sampled),
                         (back.trace_id, back.span_id, back.parent_id, back.sampled))

    def test_inject_extract_roundtrip(self):
        ctx = TraceContext.root(SECRET, sampled=True).child()
        carrier = ctx.inject({})
        self.assertEqual(set(carrier), {HEADER_NAME})
        self.assertEqual(TraceContext.extract(carrier, SECRET).span_id, ctx.span_id)

    def test_malformed_inputs_rejected(self):
        for bad in ("", "hello", "v1.aa", "v2." + "a" * 32 + ".b", "v1." + ".".join(["x"] * 5)):
            with self.assertRaises(MalformedContextError, msg=bad):
                TraceContext.deserialize(bad, SECRET)


class TestSamplingPropagation(unittest.TestCase):
    def test_sampling_decision_inherited_by_all_descendants(self):
        for decision in (True, False):
            ctx = TraceContext.root(SECRET, sampled=decision)
            for _ in range(5):
                ctx = ctx.child()
                self.assertIs(ctx.sampled, decision)

    def test_sampling_decision_survives_the_wire(self):
        for decision in (True, False):
            ctx = TraceContext.root(SECRET, sampled=decision)
            wire = ctx.child().serialize()
            self.assertIs(TraceContext.deserialize(wire, SECRET).sampled, decision)

    def test_context_is_immutable(self):
        ctx = TraceContext.root(SECRET, sampled=True)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ctx.sampled = False
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ctx.trace_id = "0" * 32

    def test_downstream_cannot_override_sampling_on_the_wire(self):
        # A downstream service flipping the sampled bit breaks the signature.
        ctx = TraceContext.root(SECRET, sampled=True)
        flipped = tamper(ctx.serialize(), 4, "0")
        with self.assertRaises(TamperedContextError):
            TraceContext.deserialize(flipped, SECRET)


class TestTamperDetection(unittest.TestCase):
    def setUp(self):
        self.raw = TraceContext.root(SECRET, sampled=True).child().serialize()

    def test_tampered_trace_id(self):
        with self.assertRaises(TamperedContextError):
            TraceContext.deserialize(tamper(self.raw, 1, "0" * 32), SECRET)

    def test_tampered_span_id(self):
        with self.assertRaises(TamperedContextError):
            TraceContext.deserialize(tamper(self.raw, 2, "f" * 16), SECRET)

    def test_tampered_parent_id(self):
        with self.assertRaises(TamperedContextError):
            TraceContext.deserialize(tamper(self.raw, 3, "0" * 16), SECRET)

    def test_tampered_signature(self):
        with self.assertRaises(TamperedContextError):
            TraceContext.deserialize(tamper(self.raw, 5, "0" * 32), SECRET)

    def test_wrong_secret(self):
        with self.assertRaises(TamperedContextError):
            TraceContext.deserialize(self.raw, OTHER_SECRET)

    def test_truncated_signature(self):
        with self.assertRaises(TamperedContextError):
            TraceContext.deserialize(self.raw[:-4] + "0000", SECRET)


class TestIntegrityValidation(unittest.TestCase):
    def _record(self, ctx, service="svc", op="op"):
        return SpanRecord.from_context(ctx, service, op)

    def test_complete_trace_is_ok(self):
        v = IntegrityValidator()
        root = TraceContext.root(SECRET, sampled=True)
        v.record(self._record(root))
        child = root.child()
        v.record(self._record(child))
        v.record(self._record(child.child()))
        self.assertTrue(v.validate().ok)

    def test_missing_parent_detected(self):
        v = IntegrityValidator()
        root = TraceContext.root(SECRET, sampled=True)
        orphan = root.child().child()  # middle span never recorded
        v.record(self._record(root))
        v.record(self._record(orphan))
        report = v.validate()
        self.assertFalse(report.ok)
        self.assertEqual([r.span_id for r in report.missing_parents], [orphan.span_id])
        self.assertIn("missing parent", str(report))

    def test_duplicate_span_id_detected(self):
        v = IntegrityValidator()
        root = TraceContext.root(SECRET, sampled=True)
        rec = self._record(root)
        v.record(rec)
        v.record(SpanRecord(rec.trace_id, rec.span_id, rec.parent_id, rec.sampled, "svc2", "op2"))
        report = v.validate()
        self.assertFalse(report.ok)
        self.assertEqual(report.duplicate_span_ids, (root.span_id,))
        self.assertIn("duplicate span id", str(report))

    def test_sampling_inconsistency_detected(self):
        v = IntegrityValidator()
        root = TraceContext.root(SECRET, sampled=True)
        v.record(self._record(root))
        v.record(SpanRecord(root.trace_id, "ab" * 8, root.span_id, False, "rogue", "op"))
        report = v.validate()
        self.assertFalse(report.ok)
        self.assertEqual(report.sampling_violations, (root.trace_id,))


class TestConcurrentChildren(unittest.TestCase):
    def test_concurrent_child_tasks_form_a_complete_trace(self):
        v = IntegrityValidator()
        root = TraceContext.root(SECRET, sampled=False)
        v.record(SpanRecord.from_context(root, "ingress", "entry"))
        worker = root.child()
        v.record(SpanRecord.from_context(worker, "worker", "dequeue"))

        def task(i):
            ctx = worker.child()
            v.record(SpanRecord.from_context(ctx, "background", f"task-{i}"))

        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(task, range(200)))

        report = v.validate()
        self.assertTrue(report.ok, str(report))
        self.assertEqual(len(v._spans), 202)
        self.assertTrue(all(s.sampled is False for s in v._spans.values()))


class TestCrossProcess(unittest.TestCase):
    def test_context_roundtrip_through_a_child_process(self):
        ctx = TraceContext.root(SECRET, sampled=True).child()
        script = (
            "import json, os, sys;"
            "sys.path.insert(0, %r);"
            "from tracelib import TraceContext;"
            "ctx = TraceContext.extract(json.loads(os.environ['CARRIER']), %r);"
            "print(ctx.child().serialize())"
            % (os.path.dirname(os.path.abspath(__file__)), SECRET)
        )
        env = dict(os.environ, CARRIER=json.dumps(ctx.inject({})))
        out = subprocess.run([sys.executable, "-c", script],
                             env=env, capture_output=True, text=True, check=True)
        child = TraceContext.deserialize(out.stdout.strip(), SECRET)
        self.assertEqual(child.trace_id, ctx.trace_id)
        self.assertEqual(child.parent_id, ctx.span_id)
        self.assertIs(child.sampled, True)

    def test_tampered_context_rejected_in_child_process(self):
        ctx = TraceContext.root(SECRET, sampled=True)
        bad = tamper(ctx.serialize(), 4, "0")
        script = (
            "import json, os, sys;"
            "sys.path.insert(0, %r);"
            "from tracelib import TraceContext, TamperedContextError;"
            "carrier = json.loads(os.environ['CARRIER']);"
            "TraceContext.extract(carrier, %r)"
            % (os.path.dirname(os.path.abspath(__file__)), SECRET)
        )
        env = dict(os.environ, CARRIER=json.dumps({HEADER_NAME: bad}))
        out = subprocess.run([sys.executable, "-c", script],
                             env=env, capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("TamperedContextError", out.stderr)


if __name__ == "__main__":
    unittest.main()
