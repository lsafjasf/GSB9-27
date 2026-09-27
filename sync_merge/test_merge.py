"""复现用例 + 修复后回归测试。运行：python3 -m unittest sync_merge.test_merge -v"""

import itertools
import random
import unittest

from sync_merge.merge_buggy import merge_field_buggy, merge_version_vector_buggy
from sync_merge.merge_fixed import (
    MISSING_SOURCE,
    Record,
    Stamp,
    merge_version_vector,
    newer_stamp,
)


def merge_all_in_order(updates, order):
    """以给定顺序把一组更新（Record 状态）合并到一个空记录上。"""
    result = Record()
    for idx in order:
        result.merge(updates[idx].clone())
    return result


def assert_converges(testcase, updates):
    """核心收敛性断言：任意顺序合并同一组更新，最终值与版本向量一致。"""
    n = len(updates)
    if n <= 6:
        orders = list(itertools.permutations(range(n)))
    else:
        rng = random.Random(42)
        orders = [list(range(n))] + [rng.sample(range(n), n) for _ in range(200)]
    expected = merge_all_in_order(updates, orders[0]).state_fingerprint()
    for order in orders[1:]:
        got = merge_all_in_order(updates, order).state_fingerprint()
        testcase.assertEqual(expected, got,
                             f"顺序 {order} 合并结果不一致: {got} != {expected}")
    return expected


def make_field_update(name, value, source, ts):
    rec = Record()
    rec.apply_field_update(name, value, source, ts)
    return rec


class ReproBuggyTests(unittest.TestCase):
    """复现：旧实现的时间戳打平 + 来源缺失 + 顺序不同 => 结果不一致。"""

    def test_buggy_same_timestamp_order_dependent(self):
        # 两侧时间戳完全相同、来源标识缺失（None）
        update_a = ("来自A的值", 1000, None)
        update_b = ("来自B的值", 1000, None)
        # 端1：A 先到，B 后到
        end1 = merge_field_buggy(merge_field_buggy(None, update_a), update_b)
        # 端2：B 先到，A 后到
        end2 = merge_field_buggy(merge_field_buggy(None, update_b), update_a)
        # 复现不一致：同一批更新，不同到达顺序，合并出不同的值
        self.assertNotEqual(end1[0], end2[0],
                            "未能复现？旧实现应当对顺序敏感")

    def test_buggy_version_vector_drops_missing_source(self):
        vv = merge_version_vector_buggy({"phone": 3}, {None: 5})
        self.assertNotIn(None, vv)  # 缺陷：来源缺失的进度被静默丢弃


class TieBreakRuleTests(unittest.TestCase):
    """决胜规则：版本号 -> 时间戳 -> 来源标识 -> 值；缺失来源归一化。"""

    def test_version_beats_timestamp(self):
        # 时钟回拨场景的关键：版本高但时间戳旧的写入必须获胜
        winner = newer_stamp(Stamp(2, 100, "a"), Stamp(1, 999, "b"))
        self.assertEqual(winner, Stamp(2, 100, "a"))

    def test_timestamp_breaks_version_tie(self):
        winner = newer_stamp(Stamp(1, 200, "a"), Stamp(1, 100, "b"))
        self.assertEqual(winner, Stamp(1, 200, "a"))

    def test_source_breaks_full_tie(self):
        winner = newer_stamp(Stamp(1, 100, "b"), Stamp(1, 100, "a"))
        self.assertEqual(winner, Stamp(1, 100, "b"))  # 字典序大者胜，确定可解释

    def test_missing_source_normalized_and_comparable(self):
        self.assertEqual(Stamp(1, 100, None).source, MISSING_SOURCE)
        winner = newer_stamp(Stamp(1, 100, None), Stamp(1, 100, "a"))
        self.assertEqual(winner, Stamp(1, 100, "a"))  # "" < "a"，确定性结果

    def test_value_breaks_identical_stamp_tie(self):
        # 同版本、同时间戳、同来源（均缺失）：按值的规范序列化决胜
        updates = [make_field_update("k", "aaa", None, 100),
                   make_field_update("k", "bbb", None, 100)]
        final, _ = assert_converges(self, updates)
        self.assertEqual(dict(final), {"k": "bbb"})

    def test_stamp_order_is_total_and_commutative(self):
        stamps = [Stamp(v, t, s) for v in (1, 2) for t in (100, 200)
                  for s in (None, "a", "b")]
        for x, y in itertools.product(stamps, stamps):
            self.assertEqual(newer_stamp(x, y), newer_stamp(y, x))  # 可交换


class ConvergenceTests(unittest.TestCase):
    """收敛性：任意端以任意顺序合并同一组更新，最终值与版本向量一致。"""

    def test_repro_scenario_now_converges(self):
        # 与复现用例相同的输入：同时间戳、来源缺失、顺序不同
        updates = [make_field_update("title", "来自A的值", None, 1000),
                   make_field_update("title", "来自B的值", None, 1000)]
        final, vv = assert_converges(self, updates)
        self.assertEqual(dict(final), {"title": "来自B的值"})  # 确定性结果
        self.assertEqual(dict(vv), {MISSING_SOURCE: 1})  # 版本向量也一致

    def test_many_devices_mixed_ops_converge(self):
        phone = Record()
        phone.apply_field_update("title", "v1", "phone", 100)
        phone.apply_field_update("title", "v3", "phone", 80)  # 时钟回拨，版本 2
        updates = [
            phone,
            make_field_update("title", "v2", "pad", 100),   # 同时戳冲突
            make_field_update("done", True, "web", 90),
            make_field_update("note", "hello", None, 100),  # 来源缺失
        ]
        final, vv = assert_converges(self, updates)
        self.assertEqual(dict(final),
                         {"title": "v3", "done": True, "note": "hello"})
        self.assertEqual(dict(vv),
                         {"phone": 2, "pad": 1, "web": 1, MISSING_SOURCE: 1})

    def test_merge_is_idempotent(self):
        updates = [make_field_update("a", 1, "x", 10),
                   make_field_update("b", 2, "y", 20)]
        once = merge_all_in_order(updates, [0, 1])
        twice = once.clone().merge(merge_all_in_order(updates, [1, 0]))
        self.assertEqual(once.state_fingerprint(), twice.state_fingerprint())


class DeleteVsUpdateTests(unittest.TestCase):
    """删除与更新竞争：墓碑参与全序比较，结果与到达顺序无关。"""

    def test_delete_vs_update_both_orders(self):
        upd = make_field_update("title", "新标题", "phone", 100)
        dele = Record()
        dele.apply_delete("web", 100)  # 同时戳的删除
        final, vv = assert_converges(self, [upd, dele])
        # 版本都是 1、时间戳都是 100，按来源决胜："web" > "phone" => 删除胜
        self.assertIsNone(final)
        self.assertEqual(dict(vv), {"phone": 1, "web": 1})

    def test_newer_update_revives_deleted_record(self):
        dele = Record()
        dele.apply_delete("web", 100)
        upd = Record()
        upd.apply_field_update("title", "旧", "web", 40)
        upd.apply_field_update("title", "复活", "web", 50)  # 版本 2，时钟回拨
        final, _ = assert_converges(self, [dele, upd])
        self.assertEqual(dict(final), {"title": "复活"})  # 版本 2 > 1，更新胜

    def test_delete_wins_over_older_update(self):
        upd = make_field_update("title", "旧值", "phone", 999)  # 时间戳新但版本旧
        dele = Record()
        dele.apply_record_update({"title": "x"}, "phone", 1)
        dele.apply_delete("phone", 1)  # 版本 2，时钟回拨到 1
        final, _ = assert_converges(self, [upd, dele])
        self.assertIsNone(final)  # 版本优先：删除胜，不受时钟回拨影响


class ClockRollbackTests(unittest.TestCase):
    """时钟回拨：逻辑版本号优先，墙钟倒退不会导致旧值复活。"""

    def test_rollback_does_not_resurrect_stale_value(self):
        rec = Record()
        rec.apply_field_update("title", "新值", "phone", ts=1000)
        rec.apply_field_update("title", "回拨写入", "phone", ts=500)  # 时钟回拨
        self.assertEqual(rec.effective_fields()["title"], "回拨写入")  # 版本 2 胜
        # 与只见过第一次写入的端合并，回拨写入仍然获胜（版本 2 > 1）
        other = make_field_update("title", "新值", "phone", 1000)
        final, _ = assert_converges(self, [rec, other])
        self.assertEqual(dict(final), {"title": "回拨写入"})


class MixedGranularityTests(unittest.TestCase):
    """字段级与记录级混合合并。"""

    def test_record_update_covers_older_field_update(self):
        rec_level = Record()
        rec_level.apply_record_update({"title": "整记录", "done": False},
                                      "web", 100)
        field_level = make_field_update("title", "字段级旧值", "phone", 90)
        final, vv = assert_converges(self, [rec_level, field_level])
        # 字段戳 (1,90,phone) < 记录戳 (1,100,web) -> 记录级覆盖 title
        self.assertEqual(dict(final), {"title": "整记录", "done": False})
        self.assertEqual(dict(vv), {"web": 1, "phone": 1})

    def test_newer_field_update_overrides_record_level(self):
        rec_level = Record()
        rec_level.apply_record_update({"title": "整记录", "done": False},
                                      "web", 100)
        field_level = Record()
        field_level.apply_field_update("title", "旧", "web", 40)
        field_level.apply_field_update("title", "字段级新值", "web", 50)  # 版本 2
        # 字段戳版本 2 > 记录戳版本 1（同一来源 web 的第二次写入）
        final, _ = assert_converges(self, [rec_level, field_level])
        self.assertEqual(dict(final), {"title": "字段级新值", "done": False})

    def test_record_delete_hides_older_fields_but_not_newer(self):
        base = make_field_update("old_field", "旧", "phone", 10)
        dele = Record()
        dele.apply_delete("web", 100)
        newer = Record()
        newer.apply_field_update("new_field", "甲", "web", 40)
        newer.apply_field_update("new_field", "新", "web", 50)  # 版本 2 > 墓碑
        final, _ = assert_converges(self, [base, dele, newer])
        self.assertEqual(dict(final), {"new_field": "新"})


class VersionVectorTests(unittest.TestCase):
    def test_merge_takes_componentwise_max(self):
        vv = merge_version_vector({"a": 2, "b": 1}, {"a": 1, "c": 5})
        self.assertEqual(vv, {"a": 2, "b": 1, "c": 5})

    def test_missing_source_not_dropped(self):
        vv = merge_version_vector({"a": 1}, {None: 3})
        self.assertEqual(vv, {"a": 1, MISSING_SOURCE: 3})


if __name__ == "__main__":
    unittest.main()
