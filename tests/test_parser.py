"""事件解析与事务边界（全产出或全不产出）的断言测试。"""
import json
import unittest

from changelog import ParseError, RawEvent, TransactionParser, parse_line


def make_line(**kw):
    return (json.dumps(kw) + "\n").encode("utf-8")


def ev(lsn, txid, op, **kw):
    return RawEvent(lsn=lsn, txid=txid, op=op, **kw)


class ParseLineTest(unittest.TestCase):
    def test_parse_insert(self):
        event = parse_line(
            make_line(lsn=2, txid=100, op="insert", table="users", pk="1",
                      after={"name": "a"})
        )
        self.assertEqual(event.lsn, 2)
        self.assertEqual(event.txid, 100)
        self.assertEqual(event.op, "insert")
        self.assertEqual(event.after, {"name": "a"})

    def test_parse_control_ops(self):
        for op in ("begin", "commit", "abort"):
            event = parse_line(make_line(lsn=1, txid=1, op=op))
            self.assertEqual(event.op, op)

    def test_invalid_json(self):
        with self.assertRaises(ParseError):
            parse_line(b"not json\n")

    def test_missing_required_field(self):
        with self.assertRaises(ParseError):
            parse_line(make_line(txid=1, op="commit"))

    def test_unknown_op(self):
        with self.assertRaises(ParseError):
            parse_line(make_line(lsn=1, txid=1, op="truncate"))

    def test_dml_requires_table_and_pk(self):
        with self.assertRaises(ParseError):
            parse_line(make_line(lsn=1, txid=1, op="insert", table="t"))


class TransactionBoundaryTest(unittest.TestCase):
    def test_commit_produces_all_changes_at_once(self):
        parser = TransactionParser()
        # commit 之前：任何事件都不产出
        self.assertIsNone(parser.feed(ev(1, 1, "begin")))
        self.assertIsNone(
            parser.feed(ev(2, 1, "insert", table="t", pk="a", after={"v": 1}))
        )
        self.assertIsNone(
            parser.feed(ev(3, 1, "insert", table="t", pk="b", after={"v": 2}))
        )
        # commit：一个事务的全部变更一次性产出
        tx = parser.feed(ev(4, 1, "commit"))
        self.assertIsNotNone(tx)
        self.assertEqual(tx.txid, 1)
        self.assertEqual(tx.commit_lsn, 4)
        self.assertEqual({c.pk for c in tx.changes}, {"a", "b"})

    def test_abort_produces_nothing(self):
        parser = TransactionParser()
        parser.feed(ev(1, 1, "begin"))
        parser.feed(ev(2, 1, "insert", table="t", pk="a", after={"v": 1}))
        self.assertIsNone(parser.feed(ev(3, 1, "abort")))
        self.assertEqual(parser.pending_txids, ())
        # abort 后的 commit 产出空事务（没有缓冲的变更）
        tx = parser.feed(ev(4, 1, "commit"))
        self.assertEqual(tx.changes, ())

    def test_uncommitted_transaction_never_emitted(self):
        parser = TransactionParser()
        parser.feed(ev(1, 1, "begin"))
        parser.feed(ev(2, 1, "delete", table="t", pk="a", before={"v": 1}))
        # 流结束也没有 commit：无任何产出
        self.assertEqual(parser.pending_txids, (1,))

    def test_interleaved_transactions_are_isolated(self):
        parser = TransactionParser()
        parser.feed(ev(1, 1, "begin"))
        parser.feed(ev(2, 2, "begin"))
        parser.feed(ev(3, 1, "insert", table="t", pk="a", after={"v": 1}))
        parser.feed(ev(4, 2, "insert", table="t", pk="b", after={"v": 2}))
        tx2 = parser.feed(ev(5, 2, "commit"))
        self.assertEqual([c.pk for c in tx2.changes], ["b"])
        tx1 = parser.feed(ev(6, 1, "commit"))
        self.assertEqual([c.pk for c in tx1.changes], ["a"])


if __name__ == "__main__":
    unittest.main()
