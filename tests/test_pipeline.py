"""端到端管线测试：事务边界、轮转、位点回退、长事务、超大行、幂等续传。"""
import os
import random
import tempfile
import unittest

from changelog import (
    ChangeLogPipeline,
    CheckpointStore,
    OversizedLineError,
    Position,
    PositionRollbackError,
)

from tests.helpers import (
    ApplyingSink,
    CollectingSink,
    CrashSink,
    LogWriter,
    SimulatedCrash,
)


class PipelineTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.logdir = os.path.join(self.tmp, "log")
        self.ckpt = os.path.join(self.tmp, "checkpoint.json")

    def make_pipeline(self, sink, **kw):
        return ChangeLogPipeline(self.logdir, self.ckpt, sink, **kw)


class TransactionAtomicityTest(PipelineTestBase):
    def test_uncommitted_transaction_produces_nothing(self):
        writer = LogWriter(self.logdir)
        writer.begin(1)
        writer.insert(1, "t", "a", {"v": 1})
        writer.close()
        sink = CollectingSink()
        # 未提交：全不产出
        self.assertEqual(self.make_pipeline(sink).run_until_caught_up(), 0)
        self.assertEqual(sink.txs, [])

    def test_commit_produces_whole_transaction(self):
        writer = LogWriter(self.logdir)
        writer.begin(1)
        writer.insert(1, "t", "a", {"v": 1})
        writer.close()
        sink = CollectingSink()
        self.make_pipeline(sink).run_until_caught_up()
        # 追加 commit 后：整个事务一次产出
        writer = LogWriter(self.logdir, start_lsn=2, start_index=0)
        writer.insert(1, "t", "b", {"v": 2})
        writer.commit(1)
        writer.close()
        self.assertEqual(self.make_pipeline(sink).run_until_caught_up(), 1)
        self.assertEqual(len(sink.txs), 1)
        self.assertEqual({c.pk for c in sink.txs[0].changes}, {"a", "b"})

    def test_aborted_transaction_produces_nothing(self):
        writer = LogWriter(self.logdir)
        writer.begin(1)
        writer.insert(1, "t", "a", {"v": 1})
        writer.abort(1)
        writer.begin(2)
        writer.insert(2, "t", "b", {"v": 2})
        writer.commit(2)
        writer.close()
        replica = {}
        sink = ApplyingSink(replica)
        self.assertEqual(self.make_pipeline(sink).run_until_caught_up(), 1)
        self.assertEqual(replica, {("t", "b"): {"v": 2}})


class RotationTest(PipelineTestBase):
    def test_log_rotation_across_segments(self):
        writer = LogWriter(self.logdir, max_segment_bytes=256)
        for txid in range(1, 31):
            writer.begin(txid)
            writer.insert(txid, "t", f"k{txid}", {"v": txid})
            writer.commit(txid)
        writer.close()
        segments = [n for n in os.listdir(self.logdir) if n.startswith("segment-")]
        self.assertGreater(len(segments), 1)  # 确认发生了轮转
        sink = CollectingSink()
        self.assertEqual(self.make_pipeline(sink).run_until_caught_up(), 30)
        commit_lsns = [tx.commit_lsn for tx in sink.txs]
        self.assertEqual(commit_lsns, sorted(commit_lsns))


class ResumeTest(PipelineTestBase):
    def _write_txs(self, count, start=1):
        writer = LogWriter(self.logdir, start_lsn=(start - 1) * 3)
        for txid in range(start, start + count):
            writer.begin(txid)
            writer.insert(txid, "t", f"k{txid}", {"v": txid})
            writer.commit(txid)
        writer.close()

    def test_restart_does_not_redeliver_confirmed(self):
        self._write_txs(5)
        sink1 = CollectingSink()
        self.assertEqual(self.make_pipeline(sink1).run_until_caught_up(), 5)
        # 重启（新的 pipeline 实例，同一位点文件）：无任何重复投递
        sink2 = CollectingSink()
        self.assertEqual(self.make_pipeline(sink2).run_until_caught_up(), 0)
        self.assertEqual(sink2.txs, [])

    def test_resume_from_checkpoint_after_restart(self):
        self._write_txs(5)
        sink1 = CollectingSink()
        self.make_pipeline(sink1).run_until_caught_up()
        # 重启后追加新事务，只投递新增的
        self._write_txs(3, start=6)
        sink2 = CollectingSink()
        self.assertEqual(self.make_pipeline(sink2).run_until_caught_up(), 3)
        self.assertEqual([tx.txid for tx in sink2.txs], [6, 7, 8])

    def test_crash_resume_never_redelivers_confirmed(self):
        self._write_txs(20)
        replica = {}
        confirmed = set()
        rng = random.Random(20260928)
        crashes = 0
        for _ in range(100):
            sink = CrashSink(replica, confirmed, budget=rng.randint(1, 4))
            try:
                self.make_pipeline(sink).run_until_caught_up()
                break
            except SimulatedCrash:
                crashes += 1
        else:
            self.fail("pipeline did not finish after 100 restarts")
        self.assertGreater(crashes, 0)  # 确认真的走过崩溃路径
        self.assertEqual(len(confirmed), 20)  # 每个已确认事务恰好一次
        self.assertEqual(
            replica, {("t", f"k{i}"): {"v": i} for i in range(1, 21)}
        )


class PositionRollbackTest(PipelineTestBase):
    def _setup_rolled_away_checkpoint(self):
        writer = LogWriter(self.logdir, max_segment_bytes=128)
        last_commit_lsn = 0
        for txid in range(1, 11):
            writer.begin(txid)
            writer.insert(txid, "t", f"k{txid}", {"v": txid})
            last_commit_lsn = writer.commit(txid)
        writer.close()
        # 位点指向已被轮转清理的段
        store = CheckpointStore(self.ckpt)
        store.save(Position(segment="segment-000000.log", offset=64,
                            last_lsn=last_commit_lsn, last_txid=10))
        os.remove(os.path.join(self.logdir, "segment-000000.log"))
        return last_commit_lsn

    def test_rollback_to_earliest_dedupes_by_lsn(self):
        self._setup_rolled_away_checkpoint()
        sink = CollectingSink()
        # 位点回退到最早可用段重读，已确认事务按 last_lsn 去重
        self.assertEqual(
            self.make_pipeline(sink, on_rollback="earliest").run_until_caught_up(),
            0,
        )
        # 新事务正常投递
        writer = LogWriter(self.logdir, start_lsn=100, start_index=99)
        writer.begin(11)
        writer.insert(11, "t", "k11", {"v": 11})
        writer.commit(11)
        writer.close()
        self.assertEqual(self.make_pipeline(sink).run_until_caught_up(), 1)
        self.assertEqual(sink.txs[0].txid, 11)

    def test_rollback_with_error_policy_raises(self):
        self._setup_rolled_away_checkpoint()
        with self.assertRaises(PositionRollbackError):
            self.make_pipeline(
                CollectingSink(), on_rollback="error"
            ).run_until_caught_up()


class OversizedLineTest(PipelineTestBase):
    def _write_big_row_tx(self, blob_size):
        writer = LogWriter(self.logdir)
        writer.begin(1)
        writer.insert(1, "t", "big", {"blob": "x" * blob_size})
        writer.commit(1)
        writer.close()

    def test_oversized_line_within_limit_is_delivered(self):
        self._write_big_row_tx(3 << 20)  # 3 MiB 单行
        replica = {}
        sink = ApplyingSink(replica)
        self.assertEqual(self.make_pipeline(sink).run_until_caught_up(), 1)
        self.assertEqual(len(replica[("t", "big")]["blob"]), 3 << 20)

    def test_oversized_line_over_limit_raises(self):
        self._write_big_row_tx(64 << 10)
        with self.assertRaises(OversizedLineError):
            self.make_pipeline(
                CollectingSink(), max_line_bytes=1024
            ).run_until_caught_up()


class LongTransactionTest(PipelineTestBase):
    def test_long_transaction_spanning_segments(self):
        writer = LogWriter(self.logdir, max_segment_bytes=4096)
        writer.begin(1)
        expected = {}
        for i in range(5000):
            row = {"i": i}
            expected[("t", f"k{i}")] = row
            writer.insert(1, "t", f"k{i}", row)
        writer.commit(1)
        writer.close()
        segments = [n for n in os.listdir(self.logdir) if n.startswith("segment-")]
        self.assertGreater(len(segments), 1)  # 长事务跨段
        sink = CollectingSink()
        # 整个长事务作为一次原子产出
        self.assertEqual(self.make_pipeline(sink).run_until_caught_up(), 1)
        self.assertEqual(len(sink.txs[0].changes), 5000)
        replica2 = {}
        ApplyingSink(replica2).deliver(sink.txs[0])
        self.assertEqual(replica2, expected)


if __name__ == "__main__":
    unittest.main()
