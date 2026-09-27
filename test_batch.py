"""批处理实现的回归测试（仅标准库 unittest）。

运行：python3 -m unittest -v test_batch.py
"""

import json
import os
import tempfile
import unittest
from collections import Counter

from batch import (
    ALLOWED_TRANSITIONS,
    BatchProcessor,
    BatchStatus,
    IllegalTransitionError,
    Record,
    SimulatedCrash,
    StateCorruptionError,
    Status,
)
from buggy_batch import BuggyBatchProcessor

RIDS = ["r1", "r2", "r3", "r4", "r5"]


class CountingSink:
    """可断言的副作用计数器：effects[rid] == 该记录副作用产生次数。"""

    def __init__(self, fail_times=None, crash_on=None):
        self.effects = Counter()
        self.calls = Counter()
        # fail_times: rid -> 前 N 次调用抛业务异常
        self._fail_times = dict(fail_times or {})
        # crash_on: (rid, 第几次调用) -> 抛 SimulatedCrash 模拟强杀
        self._crash_on = crash_on

    def handler(self, rid):
        self.calls[rid] += 1
        if self._crash_on == (rid, self.calls[rid]):
            raise SimulatedCrash(f"killed while processing {rid}")
        if self.calls[rid] <= self._fail_times.get(rid, 0):
            raise ValueError(f"transient failure on {rid}")
        self.effects[rid] += 1  # 副作用只在成功路径产生


class BatchTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = os.path.join(self._tmp.name, "state.json")

    def make(self, sink, max_attempts=3):
        return BatchProcessor(
            "batch-1", RIDS, sink.handler, self.store, max_attempts=max_attempts
        )

    def assert_idempotent(self, sink, expect_success):
        """核心幂等不变量：每条成功记录的副作用恰好一次，其余为零。"""
        for rid in RIDS:
            want = 1 if rid in expect_success else 0
            self.assertEqual(
                sink.effects[rid], want, f"{rid} side-effect count mismatch"
            )


class TestReproBug(BatchTestCase):
    """复现原始 bug：重跑重复处理已成功记录、跳过失败记录不再重试。"""

    def test_buggy_impl_duplicates_success_and_skips_failed(self):
        sink = CountingSink(fail_times={"r3": 1})
        BuggyBatchProcessor(RIDS, sink.handler, self.store).run()
        self.assertEqual(sink.effects["r3"], 0)
        # 第二次运行：r3 已不再抛错，但 buggy 实现把它永久跳过
        BuggyBatchProcessor(RIDS, sink.handler, self.store).run()
        # bug1: 已成功的记录被重复处理（副作用发生 2 次）
        for rid in ("r1", "r2", "r4", "r5"):
            self.assertEqual(sink.effects[rid], 2, f"bug not reproduced for {rid}")
        # bug2: 失败的 r3 永远不再重试
        self.assertEqual(sink.calls["r3"], 1)
        self.assertEqual(sink.effects["r3"], 0)


class TestStateMachine(unittest.TestCase):
    def test_legal_transitions(self):
        rec = Record("x")
        rec.transition(Status.PROCESSING)
        rec.transition(Status.FAILED)
        rec.transition(Status.PROCESSING)
        rec.transition(Status.SUCCESS)
        self.assertEqual(rec.status, Status.SUCCESS)

    def test_recovery_transition(self):
        rec = Record("x", status=Status.PROCESSING)
        rec.transition(Status.PENDING, recovery=True)
        self.assertEqual(rec.status, Status.PENDING)

    def test_illegal_transitions_raise(self):
        illegal = [
            (Status.PENDING, Status.SUCCESS),
            (Status.PENDING, Status.FAILED),
            (Status.PROCESSING, Status.PENDING),   # 非 recovery 路径不允许
            (Status.PROCESSING, Status.SKIPPED),
            (Status.SUCCESS, Status.PENDING),
            (Status.SUCCESS, Status.PROCESSING),
            (Status.SKIPPED, Status.PENDING),
            (Status.FAILED, Status.SUCCESS),
        ]
        for src, dst in illegal:
            with self.subTest(src=src, dst=dst):
                with self.assertRaises(IllegalTransitionError):
                    Record("x", status=src).transition(dst)

    def test_transition_table_is_total(self):
        # 迁移表覆盖所有状态，且终态无出边
        self.assertEqual(set(ALLOWED_TRANSITIONS), set(Status))
        self.assertEqual(ALLOWED_TRANSITIONS[Status.SUCCESS], set())
        self.assertEqual(ALLOWED_TRANSITIONS[Status.SKIPPED], set())


class TestRerun(BatchTestCase):
    def test_first_record_fails_then_recovers(self):
        sink = CountingSink(fail_times={"r1": 1})
        summary = self.make(sink).run()
        # 首条失败，其余成功 -> 部分成功
        self.assertEqual(summary["batch_status"], BatchStatus.PARTIAL_SUCCESS.value)
        self.assertEqual(
            (summary["success"], summary["failed"], summary["skipped"]), (4, 1, 0)
        )
        # 重跑：只重试 r1，已成功记录零副作用
        summary = self.make(sink).run()
        self.assertEqual(summary["batch_status"], BatchStatus.SUCCESS.value)
        self.assertEqual(summary["success"], 5)
        self.assertEqual(sink.calls["r1"], 2)
        for rid in ("r2", "r3", "r4", "r5"):
            self.assertEqual(sink.calls[rid], 1)
        self.assert_idempotent(sink, expect_success=set(RIDS))

    def test_last_record_fails_until_skipped(self):
        sink = CountingSink(fail_times={"r5": 99})  # 永久失败
        for _ in range(3):  # max_attempts=3，第三次后 r5 被跳过
            summary = self.make(sink).run()
        self.assertEqual(summary["batch_status"], BatchStatus.PARTIAL_SUCCESS.value)
        self.assertEqual(
            (summary["success"], summary["failed"], summary["skipped"]), (4, 0, 1)
        )
        self.assertEqual(sink.calls["r5"], 3)  # 重试到上限后不再调用
        self.assert_idempotent(sink, expect_success=set(RIDS) - {"r5"})

    def test_all_records_fail(self):
        sink = CountingSink(fail_times={rid: 99 for rid in RIDS})
        for _ in range(3):
            summary = self.make(sink).run()
        self.assertEqual(summary["batch_status"], BatchStatus.FAILED.value)
        self.assertEqual(
            (summary["success"], summary["failed"], summary["skipped"]), (0, 0, 5)
        )
        self.assertEqual(sum(sink.effects.values()), 0)
        # 全部跳过后再重跑：无任何新调用
        calls_before = dict(sink.calls)
        self.make(sink).run()
        self.assertEqual(dict(sink.calls), calls_before)

    def test_killed_mid_processing_recovers(self):
        sink = CountingSink(crash_on=("r3", 1))
        with self.assertRaises(SimulatedCrash):
            self.make(sink).run()
        # 状态文件中 r3 停留在 PROCESSING（强杀现场）
        with open(self.store, encoding="utf-8") as fh:
            saved = {r["id"]: r for r in json.load(fh)["records"]}
        self.assertEqual(saved["r3"]["status"], "processing")
        self.assertEqual(saved["r2"]["status"], "success")
        # 新实例加载：崩溃恢复 PROCESSING -> PENDING，随后重跑完成
        proc = self.make(sink)
        self.assertEqual(proc.recovered_crashes, 1)
        self.assertEqual(proc.record("r3").status, Status.PENDING)
        summary = proc.run()
        self.assertEqual(summary["batch_status"], BatchStatus.SUCCESS.value)
        self.assert_idempotent(sink, expect_success=set(RIDS))

    def test_repeated_reruns_are_noop(self):
        sink = CountingSink()
        first = self.make(sink).run()
        self.assertEqual(first["batch_status"], BatchStatus.SUCCESS.value)
        snapshot = dict(sink.calls)
        for _ in range(3):  # 重复重跑：无副作用、无新调用、摘要稳定
            summary = self.make(sink).run()
            self.assertEqual(summary, first)
        self.assertEqual(dict(sink.calls), snapshot)
        self.assert_idempotent(sink, expect_success=set(RIDS))

    def test_failed_record_retried_across_processes(self):
        # 第一次"进程"：r2 失败一次
        sink = CountingSink(fail_times={"r2": 1})
        self.make(sink).run()
        # 全新实例（模拟新进程）从断点续跑，只重试 r2
        sink2 = CountingSink()
        summary = self.make(sink2).run()
        self.assertEqual(summary["success"], 5)
        self.assertEqual(dict(sink2.calls), {"r2": 1})


class TestInvariants(BatchTestCase):
    def test_corrupt_status_rejected_on_load(self):
        self.make(CountingSink()).run()
        with open(self.store, encoding="utf-8") as fh:
            data = json.load(fh)
        data["records"][0]["status"] = "bogus"
        with open(self.store, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        with self.assertRaises(StateCorruptionError):
            self.make(CountingSink())

    def test_batch_id_mismatch_rejected(self):
        self.make(CountingSink()).run()
        with self.assertRaises(StateCorruptionError):
            BatchProcessor("other-batch", RIDS, lambda rid: None, self.store)

    def test_invariant_success_requires_attempt(self):
        proc = self.make(CountingSink())
        proc.record("r1").attempts = 0
        proc.record("r1").status = Status.SUCCESS
        with self.assertRaises(StateCorruptionError):
            proc._assert_invariants()


if __name__ == "__main__":
    unittest.main()
