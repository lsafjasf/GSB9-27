"""RevocationList 自测：判定一致性、清理上界、三态策略、边界情形。

运行：python3 -m unittest test_revocation_list -v
"""

import os
import tempfile
import unittest

from revocation_list import RevocationList, Verdict


class FakeClock:
    """可注入、可回拨的时钟。"""
    def __init__(self, t=1_000.0):
        self.t = t
    def __call__(self):
        return self.t
    def set(self, t):
        self.t = t


class RevocationListTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "revocations.jsonl")
        self.clock = FakeClock()

    def tearDown(self):
        self.tmp.cleanup()

    def new_list(self, persist=True):
        return RevocationList(path=self.path if persist else None, clock=self.clock)

    # ---------- 基本撤销与查询 ----------

    def test_revoke_and_check(self):
        rl = self.new_list()
        rl.revoke("jti-a", exp=2_000)
        self.assertEqual(rl.check("jti-a", token_exp=2_000), Verdict.REVOKED)
        self.assertEqual(rl.check("jti-b", token_exp=2_000), Verdict.NOT_REVOKED)

    def test_batch_revoke(self):
        rl = self.new_list()
        n = rl.revoke_many([(f"jti-{i}", 5_000) for i in range(1_000)])
        self.assertEqual(n, 1_000)
        self.assertEqual(len(rl), 1_000)
        self.assertEqual(rl.check("jti-0", 5_000), Verdict.REVOKED)
        self.assertEqual(rl.check("jti-999", 5_000), Verdict.REVOKED)
        self.assertEqual(rl.check("jti-1000", 5_000), Verdict.NOT_REVOKED)

    def test_duplicate_revoke_idempotent(self):
        rl = self.new_list()
        rl.revoke("jti-dup", exp=2_000)
        rl.revoke("jti-dup", exp=2_000)   # 完全重复
        rl.revoke("jti-dup", exp=1_500)   # 更早的 exp：不应缩短记录寿命
        self.assertEqual(len(rl), 1, "重复撤销不得产生额外记录")
        self.assertEqual(rl._records["jti-dup"], 2_000, "保留最大 exp")
        rl.revoke("jti-dup", exp=3_000)   # 更晚的 exp：延长记录寿命
        self.assertEqual(rl._records["jti-dup"], 3_000)
        self.assertEqual(len(rl), 1)
        self.assertEqual(rl.check("jti-dup", 3_000), Verdict.REVOKED)

    # ---------- 三态判定与清理水位 ----------

    def test_unknown_after_record_expired(self):
        rl = self.new_list()
        rl.revoke("jti-old", exp=1_500)
        self.clock.set(2_000)
        removed = rl.cleanup()          # exp=1500 <= 2000，记录被清理
        self.assertEqual(removed, 1)
        self.assertEqual(len(rl), 0)
        # 记录已过期被清理：无法判定 -> UNKNOWN（策略：不得当作未撤销）
        self.assertEqual(rl.check("jti-old", token_exp=1_500), Verdict.UNKNOWN)
        # 任何 exp <= 水位的令牌（即使从未撤销过）同样无法判定
        self.assertEqual(rl.check("jti-never", token_exp=1_800), Verdict.UNKNOWN)
        # exp 晚于水位：若被撤销记录必然还在，可安全判定未撤销
        self.assertEqual(rl.check("jti-live", token_exp=9_999), Verdict.NOT_REVOKED)

    def test_unknown_policy_without_exp(self):
        rl = self.new_list()
        self.clock.set(2_000)
        rl.cleanup()
        # 调用方不提供 exp：按未撤销处理（无法评估水位，文档化策略）
        self.assertEqual(rl.check("jti-x"), Verdict.NOT_REVOKED)

    def test_expired_token_still_revoked_before_cleanup(self):
        rl = self.new_list()
        rl.revoke("jti-exp", exp=1_500)
        self.clock.set(3_000)           # 令牌已自然过期，但尚未清理
        self.assertEqual(rl.check("jti-exp", token_exp=1_500), Verdict.REVOKED)

    # ---------- 清理上界与内存 ----------

    def test_cleanup_bounds_memory(self):
        rl = self.new_list(persist=False)
        rl.revoke_many([(f"old-{i}", 1_500) for i in range(10_000)])
        rl.revoke_many([(f"live-{i}", 9_999) for i in range(10_000)])
        before = rl.memory_usage()
        self.clock.set(2_000)
        removed = rl.cleanup()
        after = rl.memory_usage()
        print(f"\n[内存] 清理前: {before['records']} 条, 约 {before['approx_bytes']:,} 字节"
              f" | 清理后: {after['records']} 条, 约 {after['approx_bytes']:,} 字节"
              f" | 释放 {before['approx_bytes'] - after['approx_bytes']:,} 字节")
        self.assertEqual(removed, 10_000)
        self.assertEqual(after["records"], 10_000)
        self.assertLess(after["approx_bytes"], before["approx_bytes"])
        # 存活的记录不受影响
        self.assertEqual(rl.check("live-0", 9_999), Verdict.REVOKED)

    # ---------- 重启一致性 ----------

    def test_restart_consistency(self):
        rl = self.new_list()
        rl.revoke("jti-kept", exp=9_999)
        rl.revoke("jti-purged", exp=1_500)
        rl.revoke_many([(f"batch-{i}", 9_999) for i in range(100)])
        self.clock.set(2_000)
        rl.cleanup()                    # 产生快照 + 水位
        probes = [("jti-kept", 9_999), ("jti-purged", 1_500),
                  ("jti-unknown", 1_000), ("jti-live", 9_999),
                  ("batch-42", 9_999)]
        before = [rl.check(j, e) for j, e in probes]

        rl2 = self.new_list()           # 模拟重启：从磁盘重建
        after = [rl2.check(j, e) for j, e in probes]
        self.assertEqual(before, after, "重启后判定必须完全一致")
        self.assertEqual(after, [Verdict.REVOKED, Verdict.UNKNOWN,
                                 Verdict.UNKNOWN, Verdict.NOT_REVOKED,
                                 Verdict.REVOKED])
        self.assertEqual(rl2.watermark, 2_000)
        self.assertEqual(len(rl2), 101)

    def test_restart_after_cleanup_then_more_revokes(self):
        rl = self.new_list()
        rl.revoke("jti-1", exp=1_500)
        self.clock.set(2_000)
        rl.cleanup()                    # 快照落盘
        rl.revoke("jti-2", exp=9_999)   # 快照之后追加日志
        rl2 = self.new_list()
        self.assertEqual(rl2.check("jti-1", 1_500), Verdict.UNKNOWN)
        self.assertEqual(rl2.check("jti-2", 9_999), Verdict.REVOKED)

    # ---------- 时间回拨 ----------

    def test_clock_rollback_keeps_watermark_monotonic(self):
        rl = self.new_list()
        rl.revoke("jti-a", exp=1_500)
        rl.revoke("jti-b", exp=2_500)
        self.clock.set(2_000)
        rl.cleanup()                    # 水位 -> 2000，jti-a 被清理
        self.assertEqual(rl.watermark, 2_000)

        self.clock.set(500)             # 时间回拨
        removed = rl.cleanup()
        self.assertEqual(rl.watermark, 2_000, "水位不得随回拨后退")
        self.assertEqual(removed, 0, "回拨不得清理更多记录")
        # 判定与回拨前一致
        self.assertEqual(rl.check("jti-a", 1_500), Verdict.UNKNOWN)
        self.assertEqual(rl.check("jti-b", 2_500), Verdict.REVOKED)

        rl2 = self.new_list()           # 回拨 + 重启后仍一致
        self.assertEqual(rl2.watermark, 2_000)
        self.assertEqual(rl2.check("jti-a", 1_500), Verdict.UNKNOWN)
        self.assertEqual(rl2.check("jti-b", 2_500), Verdict.REVOKED)

    # ---------- 边界 ----------

    def test_empty_cleanup_and_empty_batch(self):
        rl = self.new_list()
        self.assertEqual(rl.cleanup(), 0)
        self.assertEqual(rl.revoke_many([]), 0)
        self.assertEqual(len(rl), 0)

    def test_boundary_exp_equals_watermark(self):
        rl = self.new_list()
        rl.revoke("jti-edge", exp=2_000)
        self.clock.set(2_000)
        rl.cleanup()                    # exp <= now 被清理
        self.assertEqual(len(rl), 0)
        # exp == 水位：记录可能刚被清掉，必须 UNKNOWN
        self.assertEqual(rl.check("jti-edge", token_exp=2_000), Verdict.UNKNOWN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
