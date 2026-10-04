"""事件时间窗口库的自测：准时 / 少量迟到 / 严重迟到 / 跨时区 / 边界。"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from event_time_windows import (  # noqa: E402
    WindowedAggregator,
    assign_window,
    parse_event_time,
    RejectedRecord,
    UpdateRecord,
)

HOUR = 3600
MINUTE = 60


def ev(event_id, event_time, value):
    return {"id": event_id, "event_time": event_time, "value": value}


class BoundaryRuleTests(unittest.TestCase):
    def test_window_left_closed_right_open(self):
        w = assign_window("2026-10-04T16:00:00Z", HOUR)
        self.assertEqual(str(w), "[2026-10-04T16:00:00Z, 2026-10-04T17:00:00Z)")
        # 恰好等于窗口起点：属于本窗口
        self.assertTrue(w.contains(datetime(2026, 10, 4, 16, 0, tzinfo=timezone.utc).timestamp()))
        # 恰好等于窗口终点：不属于本窗口
        self.assertFalse(w.contains(datetime(2026, 10, 4, 17, 0, tzinfo=timezone.utc).timestamp()))
        # 终点事件归属下一个窗口
        w_next = assign_window("2026-10-04T17:00:00Z", HOUR)
        self.assertEqual(w_next.start_epoch, w.end_epoch)

    def test_boundary_event_routed_to_next_window(self):
        agg = WindowedAggregator(HOUR, allowed_lateness_seconds=0)
        agg.add(ev("a", "2026-10-04T16:59:59Z", 1))
        agg.add(ev("b", "2026-10-04T17:00:00Z", 10))
        results = {r["window"]: r for r in agg.results()}
        win16 = "[2026-10-04T16:00:00Z, 2026-10-04T17:00:00Z)"
        win17 = "[2026-10-04T17:00:00Z, 2026-10-04T18:00:00Z)"
        self.assertEqual(results[win16]["count"], 1)
        self.assertEqual(results[win16]["sum"], 1)
        self.assertEqual(results[win17]["count"], 1)
        self.assertEqual(results[win17]["sum"], 10)


class OnTimeTests(unittest.TestCase):
    def test_on_time_events_aggregate(self):
        agg = WindowedAggregator(HOUR)
        results = agg.add_batch(
            [
                ev(1, "2026-10-04T16:05:00Z", 100),
                ev(2, "2026-10-04T16:40:00Z", 20),
                ev(3, "2026-10-04T17:10:00Z", 5),
            ]
        )
        self.assertTrue(all(isinstance(r, UpdateRecord) and not r.late for r in results))
        win16, win17 = agg.results()
        self.assertEqual((win16["count"], win16["sum"]), (2, 120))
        self.assertEqual((win17["count"], win17["sum"]), (1, 5))


class LateDataTests(unittest.TestCase):
    def test_slightly_late_is_corrected_with_before_after(self):
        agg = WindowedAggregator(HOUR, allowed_lateness_seconds=2 * HOUR)
        agg.add(ev(1, "2026-10-04T16:05:00Z", 100))
        agg.add(ev(2, "2026-10-04T18:00:00Z", 7))  # 推进水位线到 18:00

        win = "[2026-10-04T16:00:00Z, 2026-10-04T17:00:00Z)"
        before = {r["window"]: r for r in agg.results()}[win]
        self.assertEqual((before["count"], before["sum"]), (1, 100))

        # 16:30 的数据在 18:00 才到达：迟到 5400s，允许范围内 -> 补算
        rec = agg.add(ev(3, "2026-10-04T16:30:00Z", 25))
        self.assertIsInstance(rec, UpdateRecord)
        self.assertTrue(rec.late)
        self.assertEqual(rec.lateness_seconds, 5400)
        self.assertEqual(rec.before, {"count": 1, "sum": 100})
        self.assertEqual(rec.after, {"count": 2, "sum": 125})

        after = {r["window"]: r for r in agg.results()}[win]
        self.assertEqual((after["count"], after["sum"]), (2, 125))

    def test_severely_late_is_recorded_not_dropped(self):
        agg = WindowedAggregator(HOUR, allowed_lateness_seconds=30 * MINUTE)
        agg.add(ev(1, "2026-10-04T16:10:00Z", 1))
        agg.add(ev(2, "2026-10-04T18:00:00Z", 2))  # 水位线 18:00

        rec = agg.add(ev(3, "2026-10-04T16:00:00Z", 999))  # 迟到 2h，超范围
        self.assertIsInstance(rec, RejectedRecord)
        self.assertEqual(rec.lateness_seconds, 2 * HOUR)
        self.assertEqual(len(agg.rejected), 1)
        self.assertEqual(agg.rejected[0].event_id, 3)
        self.assertIn("超过允许迟到", rec.reason)

        # 窗口值不被污染
        win = "[2026-10-04T16:00:00Z, 2026-10-04T17:00:00Z)"
        win16 = {r["window"]: r for r in agg.results()}[win]
        self.assertEqual((win16["count"], win16["sum"]), (1, 1))

    def test_allowed_lateness_boundary_inclusive(self):
        agg = WindowedAggregator(HOUR, allowed_lateness_seconds=HOUR)
        agg.add(ev(1, "2026-10-04T18:00:00Z", 2))  # 水位线 18:00
        # 恰好迟到 1 小时（== allowed_lateness）：接受
        rec = agg.add(ev(2, "2026-10-04T17:00:00Z", 8))
        self.assertIsInstance(rec, UpdateRecord)
        self.assertEqual(rec.lateness_seconds, HOUR)
        # 再晚 1 秒：拒绝
        rec2 = agg.add(ev(3, "2026-10-04T16:59:59Z", 8))
        self.assertIsInstance(rec2, RejectedRecord)
        self.assertEqual(rec2.lateness_seconds, HOUR + 1)


class TimezoneTests(unittest.TestCase):
    def test_same_instant_different_offsets_same_window(self):
        # 2026-10-04T16:30:00Z 的不同时区写法
        utc = "2026-10-04T16:30:00Z"
        shanghai = "2026-10-05T00:30:00+08:00"      # 上海本地已“跨天”
        new_york = "2026-10-04T12:30:00-04:00"
        expected = "[2026-10-04T16:00:00Z, 2026-10-04T17:00:00Z)"
        self.assertEqual(str(assign_window(utc, HOUR)), expected)
        self.assertEqual(str(assign_window(shanghai, HOUR)), expected)
        self.assertEqual(str(assign_window(new_york, HOUR)), expected)

    def test_cross_timezone_batch(self):
        agg = WindowedAggregator(HOUR, allowed_lateness_seconds=30 * MINUTE)
        agg.add_batch(
            [
                ev("utc", "2026-10-04T16:30:00Z", 1),
                ev("sh", "2026-10-05T00:45:00+08:00", 2),   # = 16:45Z
                ev("ny", "2026-10-04T12:20:00-04:00", 4),   # = 16:20Z
            ]
        )
        win = "[2026-10-04T16:00:00Z, 2026-10-04T17:00:00Z)"
        win16 = {r["window"]: r for r in agg.results()}[win]
        self.assertEqual((win16["count"], win16["sum"]), (3, 7))

    def test_midnight_local_does_not_shift_window(self):
        # 上海时间 10-05 00:10（半夜）等价于 UTC 10-04 16:10，
        # 不得因为“本地跨天”而被算进另一个自然日。
        agg = WindowedAggregator(24 * HOUR, allowed_lateness_seconds=0)
        agg.add(ev("night", "2026-10-05T00:10:00+08:00", 3))
        (only,) = agg.results()
        # UTC 天窗口 [10-04T00Z, 10-05T00Z)
        self.assertEqual(only["window"], "[2026-10-04T00:00:00Z, 2026-10-05T00:00:00Z)")

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            parse_event_time("2026-10-04T16:00:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
