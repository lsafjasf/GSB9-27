"""对拍测试：随机工作流下，下游重放结果必须与源数据快照一致。

工作流覆盖：插入/更新/删除、事务内同一记录多次变更、abort、
长事务、超大单行、日志轮转。
"""
import os
import random
import tempfile
import unittest

from changelog import ChangeLogPipeline

from tests.helpers import ApplyingSink, CrashSink, LogWriter, SimulatedCrash

TABLE = "users"


def make_row(rng, huge=False):
    row = {
        "name": f"user-{rng.randrange(10 ** 6)}",
        "balance": rng.randrange(10 ** 9),
        "tags": [rng.choice(["a", "b", "c"]) for _ in range(rng.randrange(4))],
    }
    if huge:
        row["blob"] = "x" * rng.randint(1 << 20, 3 << 20)  # 1~3 MiB 单行
    return row


def generate_workload(directory, seed, num_txs=150):
    """生成随机工作流，返回 (源数据快照, 已提交事务数)。"""
    rng = random.Random(seed)
    writer = LogWriter(directory, max_segment_bytes=64 * 1024)
    source = {}
    committed = 0
    for txid in range(1, num_txs + 1):
        # 5% 概率长事务
        n = rng.randint(500, 2000) if rng.random() < 0.05 else rng.choice([1, 1, 2, 3, 5, 8])
        aborted = rng.random() < 0.1
        writer.begin(txid)
        staged = dict(source)  # 事务内变更先落暂存，abort 即丢弃
        for _ in range(n):
            key = (TABLE, f"user:{rng.randrange(50)}")
            exists = key in staged
            roll = rng.random()
            if not exists or roll < 0.35:
                row = make_row(rng, huge=rng.random() < 0.02)
                if exists:
                    writer.update(txid, TABLE, key[1], staged[key], row)
                else:
                    writer.insert(txid, TABLE, key[1], row)
                staged[key] = row
            elif roll < 0.6:
                writer.delete(txid, TABLE, key[1], staged.pop(key))
            else:
                row = dict(staged[key])
                row["balance"] = row["balance"] + rng.randrange(100)
                writer.update(txid, TABLE, key[1], staged[key], row)
                staged[key] = row
        if aborted:
            writer.abort(txid)
        else:
            writer.commit(txid)
            source = staged
            committed += 1
    writer.close()
    return source, committed


class ReconcileTest(unittest.TestCase):
    def test_snapshot_reconcile(self):
        for seed in (1, 7, 42):
            with self.subTest(seed=seed):
                with tempfile.TemporaryDirectory() as tmp:
                    logdir = os.path.join(tmp, "log")
                    ckpt = os.path.join(tmp, "checkpoint.json")
                    snapshot, committed = generate_workload(logdir, seed)
                    segments = [n for n in os.listdir(logdir)
                                if n.startswith("segment-")]
                    self.assertGreater(len(segments), 1)  # 确认覆盖日志轮转
                    replica = {}
                    sink = ApplyingSink(replica)
                    pipeline = ChangeLogPipeline(logdir, ckpt, sink)
                    pipeline.run_until_caught_up()
                    # 与源数据快照对拍
                    self.assertEqual(replica, snapshot)
                    self.assertEqual(sink.delivered, committed)
                    # 幂等：重放结束后再跑无任何新投递
                    self.assertEqual(pipeline.run_until_caught_up(), 0)
                    sink2 = ApplyingSink({})
                    pipeline2 = ChangeLogPipeline(logdir, ckpt, sink2)
                    self.assertEqual(pipeline2.run_until_caught_up(), 0)

    def test_crash_resume_reconcile(self):
        with tempfile.TemporaryDirectory() as tmp:
            logdir = os.path.join(tmp, "log")
            ckpt = os.path.join(tmp, "checkpoint.json")
            snapshot, committed = generate_workload(logdir, seed=20260928)
            replica = {}
            confirmed = set()
            rng = random.Random(7)
            crashes = 0
            for _ in range(500):
                sink = CrashSink(replica, confirmed, budget=rng.randint(1, 5))
                try:
                    ChangeLogPipeline(logdir, ckpt, sink).run_until_caught_up()
                    break
                except SimulatedCrash:
                    crashes += 1
            else:
                self.fail("pipeline did not finish after 500 restarts")
            self.assertGreater(crashes, 0)
            # 崩溃续传后仍与源快照一致，且已确认事务恰好投递一次
            self.assertEqual(replica, snapshot)
            self.assertEqual(len(confirmed), committed)


if __name__ == "__main__":
    unittest.main()
