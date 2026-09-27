"""cdc 变更解析库自测：幂等、续传、事务原子性、轮转、回退、长事务、超大行、快照对拍。

运行：python3 -m unittest -v   或   python3 test_cdc.py
"""

import json
import os
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cdc import (
    ChangeParser,
    LogWriter,
    Position,
    PositionFallbackError,
    list_segments,
)

TABLE = "users"


def apply_txns(state, txns):
    """把产出的事务应用到一份状态副本上（对拍用）。"""
    for txn in txns:
        for change in txn.changes:
            if change.kind == "delete":
                state.pop(change.key, None)
            else:
                state[change.key] = change.after
    return state


class CdcTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cdc-test-")
        self.log_dir = os.path.join(self.dir, "log")
        self.ckpt = os.path.join(self.dir, "checkpoint.json")
        os.makedirs(self.log_dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def new_parser(self):
        return ChangeParser(self.log_dir, self.ckpt)

    def drain(self, parser):
        """poll + confirm 全部事务，返回事务列表。"""
        txns = []
        for txn in parser.poll():
            txns.append(txn)
            parser.confirm(txn)
        return txns

    # ---------- 基本解析与合并 ----------

    def test_insert_update_delete_and_merge(self):
        writer = LogWriter(self.log_dir)
        writer.write_txn([
            ("insert", TABLE, "u1", {"name": "a", "v": 1}),
            ("update", TABLE, "u1", {"name": "a", "v": 1}, {"name": "a", "v": 2}),
            ("update", TABLE, "u1", {"name": "a", "v": 2}, {"name": "b", "v": 3}),
            ("insert", TABLE, "u2", {"name": "x"}),
            ("delete", TABLE, "u2", {"name": "x"}),  # 插入又删除，净无变更
            ("update", TABLE, "u3", {"v": 1}, {"v": 2}),
            ("delete", TABLE, "u3", {"v": 2}),
        ])
        txns = self.drain(self.new_parser())
        self.assertEqual(len(txns), 1)
        changes = {c.key: c for c in txns[0].changes}
        self.assertEqual(set(changes), {"u1", "u3"})  # u2 被抵消

        u1 = changes["u1"]
        self.assertEqual(u1.kind, "insert")  # insert+update 合并为 insert
        self.assertIsNone(u1.before)
        self.assertEqual(u1.after, {"name": "b", "v": 3})  # 最新状态
        self.assertEqual([s["op"] for s in u1.sequence], ["insert", "update", "update"])

        u3 = changes["u3"]
        self.assertEqual(u3.kind, "delete")  # update+delete 合并为 delete
        self.assertEqual(u3.before, {"v": 1})  # 最早的 before
        self.assertEqual([s["op"] for s in u3.sequence], ["update", "delete"])

    def test_aborted_transaction_produces_nothing(self):
        writer = LogWriter(self.log_dir)
        txid = writer.begin()
        writer.insert(txid, TABLE, "u1", {"v": 1})
        writer.abort(txid)
        self.assertEqual(self.drain(self.new_parser()), [])

    # ---------- 事务原子性（断言测试） ----------

    def test_transaction_atomicity_all_or_nothing(self):
        writer = LogWriter(self.log_dir)
        txid = writer.begin()
        writer.insert(txid, TABLE, "u1", {"v": 1})
        writer.insert(txid, TABLE, "u2", {"v": 2})
        # commit 尚未落盘：一行都不允许产出
        parser = self.new_parser()
        self.assertEqual(list(parser.poll()), [])

        writer.insert(txid, TABLE, "u3", {"v": 3})
        writer.commit(txid)
        # commit 之后：三行必须在同一个事务里一次产出
        txns = list(parser.poll())
        self.assertEqual(len(txns), 1)
        self.assertEqual({c.key for c in txns[0].changes}, {"u1", "u2", "u3"})

        # 模拟崩溃：不 confirm，直接重启
        parser2 = self.new_parser()
        txns2 = list(parser2.poll())
        # 要么整体重投，要么没有——绝不出现部分行
        self.assertEqual(len(txns2), 1)
        self.assertEqual({c.key for c in txns2[0].changes}, {"u1", "u2", "u3"})
        parser2.confirm(txns2[0])

        # 确认后重启：不再产出
        self.assertEqual(list(self.new_parser().poll()), [])

    # ---------- 幂等与断点续传 ----------

    def test_idempotent_restart_no_redelivery(self):
        writer = LogWriter(self.log_dir)
        writer.write_txn([("insert", TABLE, "u1", {"v": 1})])
        writer.write_txn([("insert", TABLE, "u2", {"v": 2})])
        delivered = [t.commit_lsn for t in self.drain(self.new_parser())]
        self.assertEqual(len(delivered), 2)
        # 重启后重复 poll 多次：已确认的变更不得重复投递
        for _ in range(3):
            self.assertEqual(self.drain(self.new_parser()), [])

    def test_resume_from_checkpoint(self):
        writer = LogWriter(self.log_dir)
        writer.write_txn([("insert", TABLE, "u1", {"v": 1})])
        writer.write_txn([("insert", TABLE, "u2", {"v": 2})])

        parser = self.new_parser()
        txns = list(parser.poll())
        self.assertEqual(len(txns), 2)
        parser.confirm(txns[0])  # 只确认第一个就"崩溃"

        parser2 = self.new_parser()  # 重启续传
        txns2 = list(parser2.poll())
        self.assertEqual(len(txns2), 1)
        self.assertEqual(txns2[0].changes[0].key, "u2")  # u1 不重投
        parser2.confirm(txns2[0])
        self.assertEqual(list(self.new_parser().poll()), [])

    def test_position_rollback_still_idempotent(self):
        writer = LogWriter(self.log_dir)
        writer.write_txn([("insert", TABLE, "u1", {"v": 1})])
        parser = self.new_parser()
        txn = self.drain(parser)[0]
        confirmed_lsn = txn.commit_lsn

        # 位点被回退到文件开头，但 lsn 高水位保留 -> 已确认事件仍被跳过
        first_segment = list_segments(self.log_dir)[0]
        parser.reset(Position(segment=first_segment, offset=0, lsn=confirmed_lsn))
        self.assertEqual(list(parser.poll()), [])

    # ---------- 日志轮转 ----------

    def test_log_rotation(self):
        writer = LogWriter(self.log_dir, max_segment_bytes=200)
        for i in range(10):
            writer.write_txn([("insert", TABLE, "u%d" % i, {"v": i})])
        self.assertGreater(len(list_segments(self.log_dir)), 1)  # 确实发生了轮转

        txns = self.drain(self.new_parser())
        self.assertEqual(len(txns), 10)  # 跨段连续读取，一个不少
        self.assertEqual([t.changes[0].key for t in txns], ["u%d" % i for i in range(10)])
        # 重启后从最后一段的位点继续，无重复
        self.assertEqual(self.drain(self.new_parser()), [])

    def test_long_transaction_spanning_segments(self):
        writer = LogWriter(self.log_dir, max_segment_bytes=150)
        txid = writer.begin()
        for i in range(20):  # 事务体跨多个段
            writer.insert(txid, TABLE, "k%d" % i, {"v": i})
        writer.commit(txid)
        self.assertGreater(len(list_segments(self.log_dir)), 2)

        txns = self.drain(self.new_parser())
        self.assertEqual(len(txns), 1)  # 跨段长事务仍作为一个整体产出
        self.assertEqual(len(txns[0].changes), 20)

    # ---------- 超大单行变更 ----------

    def test_huge_single_row(self):
        writer = LogWriter(self.log_dir, max_segment_bytes=1024)
        big_blob = "x" * (5 * 1024 * 1024)  # 5MB 单行
        writer.write_txn([("insert", TABLE, "big", {"blob": big_blob})])
        writer.write_txn([("update", TABLE, "big", {"blob": big_blob}, {"blob": big_blob + "y"})])

        txns = self.drain(self.new_parser())
        self.assertEqual(len(txns), 2)
        self.assertEqual(txns[0].changes[0].after["blob"], big_blob)
        self.assertEqual(txns[1].changes[0].after["blob"], big_blob + "y")

    # ---------- 位点回退（位点过旧 -> 快照恢复） ----------

    def test_position_fallback_and_snapshot_recovery(self):
        writer = LogWriter(self.log_dir, max_segment_bytes=120)
        source = {}
        # 先写一批事务，让快照位点落在较新的段里
        for i in range(5):
            source["s%d" % i] = {"v": i}
            writer.write_txn([("insert", TABLE, "s%d" % i, {"v": i})])
        snapshot_position = writer.current_position()
        snapshot = dict(source)
        # 快照之后源端继续变化
        for i in range(10):
            source["u%d" % i] = {"v": i}
            writer.write_txn([("insert", TABLE, "u%d" % i, {"v": i})])

        # 位点指向第一段，但快照位点之前的段已被轮转清理
        stale = Position(segment=list_segments(self.log_dir)[0], offset=0, lsn=0)
        for segment in list_segments(self.log_dir):
            if segment >= snapshot_position.segment:
                break
            os.unlink(os.path.join(self.log_dir, segment))

        parser = self.new_parser()
        parser.reset(stale)
        with self.assertRaises(PositionFallbackError):
            list(parser.poll())

        # 恢复流程：加载快照 + 快照水位位点，继续增量解析
        parser.reset(snapshot_position)
        replica = apply_txns(dict(snapshot), self.drain(parser))
        self.assertEqual(replica, source)  # 对拍一致

    # ---------- 与源数据快照对拍 ----------

    def test_snapshot_reconciliation_with_aborts(self):
        rng = random.Random(7)
        writer = LogWriter(self.log_dir, max_segment_bytes=1024)
        source = {"u%d" % i: {"v": i} for i in range(30)}
        snapshot = dict(source)
        snapshot_position = writer.current_position()

        for _ in range(100):
            txid = writer.begin()
            backup = dict(source)
            for _ in range(rng.randint(1, 4)):
                key = "u%d" % rng.randint(0, 40)
                action = rng.choice(["insert", "update", "delete"])
                if key not in source and action != "delete":
                    # insert 隐含记录此前不存在（真实 CDC 语义）
                    after = {"v": rng.randint(0, 999)}
                    writer.insert(txid, TABLE, key, after)
                    source[key] = after
                elif action in ("insert", "update"):
                    before = source[key]
                    after = {"v": rng.randint(0, 999)}
                    writer.update(txid, TABLE, key, before, after)
                    source[key] = after
                elif key in source:
                    writer.delete(txid, TABLE, key, source[key])
                    del source[key]
            if rng.random() < 0.2:
                writer.abort(txid)
                source = backup  # 回滚源端状态
            else:
                writer.commit(txid)

        parser = self.new_parser()
        parser.reset(snapshot_position)
        replica = apply_txns(dict(snapshot), self.drain(parser))
        self.assertEqual(replica, source)  # 对拍：解析结果 + 快照 == 源端最终状态


if __name__ == "__main__":
    unittest.main(verbosity=2)
