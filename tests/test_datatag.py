"""datatag 自测：合并规则、继承传播、丢失检测、边界用例。

运行：python3 -m unittest discover -s tests -v
"""

import unittest

from datatag import (
    CONFIDENTIAL,
    INTERNAL,
    PUBLIC,
    RESTRICTED,
    SECRET,
    UNLABELED,
    DerivationOp,
    EnvelopeError,
    Label,
    Level,
    TaggedData,
    UnknownLabelError,
    merge_labels,
    pack_envelope,
    record_handoff,
    verify_envelope,
)
from datatag.labels import (
    ALL_RESTRICTIONS,
    RESTRICTION_NO_EXPORT,
    RESTRICTION_PII,
    merge_rule_table,
)


class TestMergeRules(unittest.TestCase):
    """合并规则：只能收紧，覆盖同标签/冲突/无标签。"""

    def test_same_label_merge_is_identity(self):
        for label in (PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED, SECRET):
            result = merge_labels([label, label])
            self.assertEqual(result.label, label)
            self.assertFalse(result.has_conflict)
            self.assertFalse(result.tightened)

    def test_level_conflict_tightens_to_stricter(self):
        result = merge_labels([INTERNAL, CONFIDENTIAL])
        self.assertEqual(result.label, CONFIDENTIAL)
        self.assertTrue(result.tightened)
        self.assertEqual(result.conflicts[0].dimension, "level")
        self.assertEqual(result.conflicts[0].resolved, "CONFIDENTIAL")

    def test_merge_is_symmetric(self):
        left = merge_labels([INTERNAL, SECRET]).label
        right = merge_labels([SECRET, INTERNAL]).label
        self.assertEqual(left, right)

    def test_restriction_conflict_takes_union(self):
        a = Label(Level.CONFIDENTIAL, frozenset({RESTRICTION_PII}))
        b = Label(Level.CONFIDENTIAL, frozenset({RESTRICTION_NO_EXPORT}))
        result = merge_labels([a, b])
        self.assertEqual(
            result.label.restrictions,
            frozenset({RESTRICTION_PII, RESTRICTION_NO_EXPORT}),
        )
        dims = {c.dimension for c in result.conflicts}
        self.assertIn("restriction:PII", dims)
        self.assertIn("restriction:NO_CROSS_BORDER", dims)

    def test_merge_never_loosens_for_all_pairs(self):
        labels = [
            UNLABELED, PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED, SECRET,
            Label(Level.INTERNAL, frozenset({RESTRICTION_PII})),
            Label(Level.SECRET, frozenset(ALL_RESTRICTIONS)),
        ]
        for a in labels:
            for b in labels:
                merged = merge_labels([a, b]).label
                self.assertTrue(merged.covers(a), f"{merged} 未覆盖 {a}")
                self.assertTrue(merged.covers(b), f"{merged} 未覆盖 {b}")

    def test_multi_source_merge(self):
        result = merge_labels([PUBLIC, INTERNAL, CONFIDENTIAL])
        self.assertEqual(result.label, CONFIDENTIAL)

    def test_empty_merge_rejected(self):
        with self.assertRaises(ValueError):
            merge_labels([])

    def test_rule_table_is_complete_and_consistent(self):
        rows = merge_rule_table()
        self.assertEqual(len(rows), 36)  # 6 级 x 6 级
        for row in rows:
            merged = merge_labels(
                [Label(Level[row["left"]]), Label(Level[row["right"]])]
            ).label
            self.assertEqual(merged.level.name, row["result"])


class TestUnlabeled(unittest.TestCase):
    """含无标签数据的合并与告警。"""

    def test_unlabeled_plus_labeled_yields_labeled(self):
        result = merge_labels([UNLABELED, INTERNAL])
        self.assertEqual(result.label, INTERNAL)
        self.assertTrue(result.unlabeled_input)

    def test_unlabeled_is_not_public(self):
        result = merge_labels([UNLABELED, SECRET])
        self.assertEqual(result.label, SECRET)

    def test_all_unlabeled_stays_unlabeled(self):
        result = merge_labels([UNLABELED, UNLABELED])
        self.assertEqual(result.label, UNLABELED)
        self.assertTrue(result.unlabeled_input)

    def test_unlabeled_source_flagged_in_envelope(self):
        src = TaggedData.source("d1", {"x": 1}, UNLABELED, "legacy")
        env = pack_envelope(src)
        report = verify_envelope(env)
        self.assertFalse(report.ok)
        self.assertEqual(report.status, "UNLABELED_SOURCE")


class TestLineage(unittest.TestCase):
    """派生数据继承来源标签，继承链可审计。"""

    def _pipeline(self):
        crm = TaggedData.source(
            "crm", {"r": 1},
            Label(Level.CONFIDENTIAL, frozenset({RESTRICTION_PII})), "crm",
        )
        legacy = TaggedData.source("legacy", {"r": 2}, UNLABELED, "legacy")
        merged = TaggedData.derive(
            "etl", [crm, legacy],
            DerivationOp(system="etl", transform="join"), {"r": 3},
        )
        report = TaggedData.derive(
            "bi", [merged], DerivationOp(system="bi", transform="agg"), {"r": 4},
        )
        return crm, legacy, merged, report

    def test_derived_inherits_strictest(self):
        _, _, merged, report = self._pipeline()
        self.assertEqual(merged.label.level, Level.CONFIDENTIAL)
        self.assertIn(RESTRICTION_PII, merged.label.restrictions)
        self.assertEqual(report.label, merged.label)

    def test_chain_contains_all_ancestors(self):
        _, _, _, report = self._pipeline()
        chain = report.lineage.chain_to("bi")
        self.assertEqual([n.data_id for n in chain], ["crm", "legacy", "etl", "bi"])

    def test_lineage_audit_passes_for_valid_chain(self):
        _, _, _, report = self._pipeline()
        self.assertEqual(report.lineage.audit(), [])

    def test_lineage_audit_detects_weakening(self):
        _, _, merged, _ = self._pipeline()
        weakened = TaggedData(
            data_id="bad",
            payload={},
            label=PUBLIC,  # 比来源 CONFIDENTIAL 宽松
            lineage=merged.lineage,
        )
        from datatag.lineage import LineageNode
        merged.lineage.add(
            LineageNode(
                data_id="bad", label=PUBLIC, created_at="",
                system="x", parents=("etl",),
            )
        )
        problems = merged.lineage.audit()
        self.assertTrue(problems)
        self.assertIn("bad", problems[0])

    def test_derive_without_parents_rejected(self):
        with self.assertRaises(ValueError):
            TaggedData.derive(
                "x", [], DerivationOp(system="s", transform="t"), {}
            )


class TestTransport(unittest.TestCase):
    """跨系统交接：丢标签、改宽、篡改、断链都能被检测。"""

    def _data(self):
        return TaggedData.source(
            "d1", {"x": 1},
            Label(Level.CONFIDENTIAL, frozenset({RESTRICTION_PII})), "crm",
        )

    def test_ok_handoff(self):
        env = pack_envelope(self._data())
        report = record_handoff(env, "crm", "etl", received_label_raw=env["label"])
        self.assertTrue(report.ok)
        self.assertEqual(report.status, "OK")
        self.assertTrue(verify_envelope(env).ok)

    def test_missing_label_detected(self):
        env = pack_envelope(self._data())
        report = record_handoff(env, "crm", "etl", received_label_raw=None)
        self.assertFalse(report.ok)
        self.assertEqual(report.status, "MISSING_LABEL")
        final = verify_envelope(env)
        self.assertFalse(final.ok)
        self.assertTrue(any("MISSING_LABEL" in v for v in final.violations))

    def test_weakened_label_detected(self):
        env = pack_envelope(self._data())
        weakened = {"level": "INTERNAL", "restrictions": []}
        report = record_handoff(env, "crm", "etl", received_label_raw=weakened)
        self.assertEqual(report.status, "LABEL_WEAKENED")
        self.assertFalse(verify_envelope(env).ok)

    def test_dropped_restriction_detected_as_weakening(self):
        env = pack_envelope(self._data())
        dropped = {"level": "CONFIDENTIAL", "restrictions": []}  # PII 被丢
        report = record_handoff(env, "crm", "etl", received_label_raw=dropped)
        self.assertEqual(report.status, "LABEL_WEAKENED")

    def test_unknown_label_rejected(self):
        env = pack_envelope(self._data())
        forged = {"level": "PUBLICISH", "restrictions": []}
        report = record_handoff(env, "crm", "etl", received_label_raw=forged)
        self.assertEqual(report.status, "LABEL_TAMPERED")

    def test_unknown_restriction_rejected(self):
        with self.assertRaises(UnknownLabelError):
            Label(Level.INTERNAL, frozenset({"MADE_UP"}))

    def test_broken_lineage_detected(self):
        env = pack_envelope(self._data())
        env["lineage"] = []
        report = verify_envelope(env)
        self.assertFalse(report.ok)
        self.assertTrue(any("LINEAGE_BROKEN" in v for v in report.violations))

    def test_envelope_missing_field_rejected(self):
        with self.assertRaises(EnvelopeError):
            verify_envelope({"data_id": "x"})

    def test_handoff_trail_recorded(self):
        env = pack_envelope(self._data())
        record_handoff(env, "crm", "etl", received_label_raw=env["label"])
        record_handoff(env, "etl", "bi", received_label_raw=None)
        self.assertEqual(len(env["handoffs"]), 2)
        self.assertEqual(env["handoffs"][1]["status"], "MISSING_LABEL")


if __name__ == "__main__":
    unittest.main()
