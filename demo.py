"""端到端演示：事件时间切窗 + 迟到补算 + 严重迟到留档 + 跨时区。

运行：python3 demo.py
窗口大小 1 小时（UTC 对齐），允许迟到 30 分钟。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from event_time_windows import WindowedAggregator, RejectedRecord, UpdateRecord

HOUR = 3600
MINUTE = 60

# 事件故意按“处理顺序”（到达顺序）给出，而非事件时间顺序：
# - e1 准时
# - e2 准时（推进水位线到 17:30Z）
# - e3 跨时区：上海本地已过半夜 00:10，但同一时刻是 UTC 16:10
# - e4 少量迟到（16:40Z，迟到 50 分钟）
#   -> 先演示“在允许范围内”的场景时不会被拒，所以放在单独场景 B 中。
# 为了同时覆盖“少量迟到被补算”和“严重迟到被留档”，演示分两个聚合器。

SCENARIO = [
    # (id, 事件时间(带时区), value, 备注)
    ("e1", "2026-10-04T16:05:00Z", 100, "准时"),
    ("e3", "2026-10-05T00:10:00+08:00", 5, "跨时区：上海半夜 = UTC 16:10，准时到达"),
    ("e2", "2026-10-04T17:30:00Z", 20, "准时，水位线 -> 17:30Z"),
    ("e4", "2026-10-04T16:40:00Z", 40, "迟到 50 分钟，允许范围内 -> 补算"),
    ("e5", "2026-10-04T15:00:00Z", 999, "严重迟到 2.5 小时 -> 留档"),
]


def print_title(text):
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


def run(allowed_lateness):
    label = f"{allowed_lateness // MINUTE} 分钟"
    print_title(f"场景：允许迟到 {label}（窗口 1 小时，左闭右开）")
    agg = WindowedAggregator(HOUR, allowed_lateness_seconds=allowed_lateness)

    print("\n[1] 逐条摄入与窗口归属")
    print(f"{'事件':<4}{'事件时间(UTC)':<22}{'归属窗口':<42}{'值':>6}  状态")
    for event_id, event_time, value, note in SCENARIO:
        result = agg.add({"id": event_id, "event_time": event_time, "value": value})
        a = agg.assignments[-1]
        status = a["status"]
        if status == "late":
            status += f"（迟到 {int(a['lateness_seconds'] // MINUTE)} 分钟，补算）"
        elif status == "rejected":
            status += "（超范围，留档）"
        print(
            f"{a['id']:<4}{a['event_time']:<22}{a['window']:<42}{a['value']:>6}  {status}"
        )
        print(f"     原始时间 {event_time}  # {note}")

    print("\n[2] 迟到补算的更新前后窗口值")
    late_updates = [u for u in agg.updates if u.late]
    if not late_updates:
        print("  （本场景下没有可补算的迟到数据）")
    for u in late_updates:
        print(f"  事件 {u.event_id} @ {u.event_time_iso} 迟到 {int(u.lateness_seconds // MINUTE)} 分钟")
        print(f"    窗口 {u.window}")
        print(f"    更新前: count={u.before['count']}, sum={u.before['sum']}")
        print(f"    更新后: count={u.after['count']}, sum={u.after['sum']}")

    print("\n[3] 严重迟到（超出允许范围）单独记录")
    if not agg.rejected:
        print("  无")
    for r in agg.rejected:
        print(f"  event_id={r.event_id}  event_time={r.event_time_iso}  value={r.value}")
        print(f"    原应归属窗口: {r.window}")
        print(f"    原因: {r.reason}")

    print("\n[4] 最终窗口结果")
    for row in agg.results():
        print(f"  {row['window']}  count={row['count']}, sum={row['sum']}")
    print(f"  最终水位线: {agg.watermark_iso()}")


def boundary_demo():
    print_title("边界用例：左闭右开 [start, end)")
    agg = WindowedAggregator(HOUR, allowed_lateness_seconds=30 * MINUTE)
    cases = [
        ("b1", "2026-10-04T16:00:00Z", 1, "恰好窗口起点 -> 16 点窗口"),
        ("b2", "2026-10-04T16:59:59Z", 1, "窗口结束前 1 秒 -> 16 点窗口"),
        ("b3", "2026-10-04T17:00:00Z", 1, "恰好窗口终点 -> 17 点窗口（不含于 16 点）"),
        ("b4", "2026-10-04T17:00:00Z", 1, "允许迟到边界: 恰迟到 30min == 阈值，接受"),
    ]
    for event_id, event_time, value, note in cases[:3]:
        agg.add({"id": event_id, "event_time": event_time, "value": value})
        print(f"  {event_id} {event_time} -> {agg.assignments[-1]['window']}  # {note}")

    # 单独构造“恰好等于允许迟到阈值”的边界
    agg2 = WindowedAggregator(HOUR, allowed_lateness_seconds=30 * MINUTE)
    agg2.add({"id": "w", "event_time": "2026-10-04T17:30:00Z", "value": 1})
    rec = agg2.add({"id": "b4", "event_time": "2026-10-04T17:00:00Z", "value": 1})
    kind = "接受并补算" if isinstance(rec, UpdateRecord) else "拒绝"
    print(f"  b4 水位线 17:30Z 时到达 17:00Z 数据（恰好迟到 30 分钟）-> {kind}  # {cases[3][3]}")
    rec2 = agg2.add({"id": "b5", "event_time": "2026-10-04T16:59:59Z", "value": 1})
    kind2 = "拒绝并留档" if isinstance(rec2, RejectedRecord) else "接受"
    print("  b5 同水位线下 16:59:59Z（迟到 30 分 1 秒）-> " + kind2)

    print("\n  边界聚合结果:")
    for row in agg.results():
        print(f"    {row['window']}  count={row['count']}, sum={row['sum']}")


def timezone_demo():
    print_title("跨时区用例：同一时刻，不同写法，同一窗口")
    agg = WindowedAggregator(HOUR, allowed_lateness_seconds=30 * MINUTE)
    rows = [
        ("t1", "2026-10-04T16:30:00Z", 1, "UTC"),
        ("t2", "2026-10-05T00:30:00+08:00", 2, "上海（本地已跨天到次日）"),
        ("t3", "2026-10-04T12:30:00-04:00", 4, "纽约"),
    ]
    for event_id, event_time, value, note in rows:
        agg.add({"id": event_id, "event_time": event_time, "value": value})
        a = agg.assignments[-1]
        print(f"  {event_id} {event_time:<28} -> {a['event_time']}  {a['window']}  # {note}")
    (only,) = agg.results()
    print(f"  归一化后三条数据同一窗口: count={only['count']}, sum={only['sum']}")


if __name__ == "__main__":
    run(allowed_lateness=60 * MINUTE)   # e4(迟到50min) 被补算，e5 被留档
    boundary_demo()
    timezone_demo()
