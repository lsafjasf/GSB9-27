"""同一记录多次变更合并为最新状态的测试（保留变更序列）。"""
import unittest

from changelog import RawEvent, merge_record_events


def ev(lsn, op, **kw):
    return RawEvent(lsn=lsn, txid=1, op=op, table="t", pk="k", **kw)


class MergeRecordTest(unittest.TestCase):
    def test_insert_then_update_merges_to_insert_with_latest_image(self):
        change = merge_record_events([
            ev(1, "insert", after={"v": 1}),
            ev(2, "update", before={"v": 1}, after={"v": 2}),
            ev(3, "update", before={"v": 2}, after={"v": 3}),
        ])
        self.assertEqual(change.op, "insert")
        self.assertIsNone(change.before)
        self.assertEqual(change.after, {"v": 3})
        # 变更序列完整保留
        self.assertEqual([e.op for e in change.history],
                         ["insert", "update", "update"])
        self.assertEqual([e.lsn for e in change.history], [1, 2, 3])

    def test_updates_merge_to_first_before_and_last_after(self):
        change = merge_record_events([
            ev(1, "update", before={"v": 0}, after={"v": 1}),
            ev(2, "update", before={"v": 1}, after={"v": 2}),
        ])
        self.assertEqual(change.op, "update")
        self.assertEqual(change.before, {"v": 0})
        self.assertEqual(change.after, {"v": 2})

    def test_update_then_delete_merges_to_delete(self):
        change = merge_record_events([
            ev(1, "update", before={"v": 0}, after={"v": 1}),
            ev(2, "delete", before={"v": 1}),
        ])
        self.assertEqual(change.op, "delete")
        self.assertEqual(change.before, {"v": 0})
        self.assertIsNone(change.after)

    def test_insert_then_delete_merges_to_none(self):
        change = merge_record_events([
            ev(1, "insert", after={"v": 1}),
            ev(2, "delete", before={"v": 1}),
        ])
        self.assertEqual(change.op, "none")
        self.assertEqual(len(change.history), 2)

    def test_delete_then_insert_merges_to_update(self):
        change = merge_record_events([
            ev(1, "delete", before={"v": 1}),
            ev(2, "insert", after={"v": 9}),
        ])
        self.assertEqual(change.op, "update")
        self.assertEqual(change.before, {"v": 1})
        self.assertEqual(change.after, {"v": 9})

    def test_single_event_passthrough(self):
        change = merge_record_events([ev(1, "insert", after={"v": 1})])
        self.assertEqual(change.op, "insert")
        self.assertEqual(change.after, {"v": 1})
        self.assertEqual(len(change.history), 1)


if __name__ == "__main__":
    unittest.main()
