"""复现脚本：同一场景下对比有缺陷实现与修复实现。

场景：窗口 W = [999000, 1002600)，在 W 内 999900 时刻累计到 10，
进程重启（落盘点之后），在 1000100 时刻（同一窗口）又增加 5。

运行：python3 reproduce_bug.py
退出码：复现到 bug 且修复版守恒 -> 0；否则 -> 1
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from buggy_metrics import BuggyMetrics
from metrics import MetricsStore

WINDOW = 3600
BASE = 997200  # 窗口起点（3600 的整数倍）


def run_buggy(path):
    clocks = [BASE + 900, BASE + 1100]  # 重启前、重启后（同一窗口）
    m1 = BuggyMetrics(path, window_seconds=WINDOW, clock=lambda: clocks[0])
    m1.incr(10)
    m1.flush()          # 定期落盘后进程退出
    curve_before = m1.window_curve()
    # ---- 重启 ----
    m2 = BuggyMetrics(path, window_seconds=WINDOW, clock=lambda: clocks[1])
    m2.incr(5)
    curve_after = m2.window_curve()
    return curve_before, curve_after, m2._total


def run_fixed(path):
    clocks = [BASE + 900, BASE + 1100]
    s1 = MetricsStore(path, window_seconds=WINDOW, clock=lambda: clocks[0])
    s1.incr(10)
    s1.flush()
    total_before = s1.total
    # ---- 重启 ----
    s2 = MetricsStore(path, window_seconds=WINDOW, clock=lambda: clocks[1])
    s2.incr(5)
    s2.flush()
    return total_before, s2.total, s2.window_aggregate(BASE)


def main():
    tmp = tempfile.mkdtemp()

    curve_before, curve_after, buggy_total = run_buggy(
        os.path.join(tmp, "buggy.state"))
    print("== 有缺陷实现 ==")
    print("  重启前窗口曲线:", curve_before)
    print("  重启后窗口曲线:", curve_after)
    print("  重启后 total  :", buggy_total, "（期望 15，实际从零开始）")
    buggy_regressed = curve_after.get(BASE, 0) < curve_before.get(BASE, 0)
    print("  曲线倒退      :", buggy_regressed)

    total_before, total_after, window_agg = run_fixed(
        os.path.join(tmp, "fixed.state"))
    print("== 修复实现 ==")
    print("  重启前 total  :", total_before)
    print("  重启后 total  :", total_after)
    print("  窗口聚合值    :", window_agg, "（== 重启前后之和 10+5）")
    conserved = (window_agg == 15 and total_after == 15)
    monotonic = total_after >= total_before
    print("  守恒          :", conserved, " 单调:", monotonic)

    ok = buggy_regressed and conserved and monotonic
    print("结论:", "bug 已复现，修复版守恒且单调" if ok else "不符合预期")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
