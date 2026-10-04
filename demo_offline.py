"""离线模式演示：模拟断网 -> 降级 -> 恢复限速重发，输出关键指标。

运行：python3 demo_offline.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collector.buffer import DiskBuffer
from collector.clock import FakeClock
from collector.collector import Collector
from tests.test_offline_mode import FakeTransport

RATE = 100.0      # 限速：100 条/秒
BATCH = 10        # 单批上限 10 条
CAP = 500         # 缓冲上界 500 条


def main():
    tmpdir = tempfile.mkdtemp(prefix="collector_demo_")
    try:
        clock = FakeClock()
        transport = FakeTransport(online=True)
        buf = DiskBuffer(tmpdir, max_records=CAP)
        coll = Collector(buf, transport, clock=clock,
                         resend_rate=RATE, batch_size=BATCH)

        print("== 阶段 1：正常在线，实时直发 ==")
        for i in range(200):
            coll.emit({"v": i})
        print(f"  实时上报 {coll.sent_online} 条，缓冲积压 {coll.backlog}，丢弃 {coll.dropped}")

        print("== 阶段 2：长时间断网，数据落盘，上界降级 ==")
        transport.online = False
        for i in range(800):  # 远超上界 500
            coll.emit({"v": 200 + i})
        print(f"  断网期间采集 800 条，缓冲上界 {CAP}")
        print(f"  落盘积压 {coll.backlog} 条，丢弃 {coll.dropped} 条（策略：丢弃最旧）")

        print("== 阶段 3：网络恢复，限速重发 ==")
        transport.online = True
        metrics = coll.drain()
        print(f"  重发 {metrics.sent} 条，逻辑耗时 {metrics.elapsed:.2f} 秒")
        print(f"  实际重发速率 {metrics.effective_rate:.1f} 条/秒（限速 {RATE:.0f} 条/秒，单批上限 {BATCH}）")
        print(f"  服务端收到的最大单批 {max(transport.batch_sizes)} 条")
        print("  积压下降曲线（相对秒 -> 剩余积压）：")
        step = max(1, len(metrics.samples) // 10)
        for t, pending in metrics.samples[::step]:
            print(f"    t={t:6.2f}s  backlog={pending}")
        print(f"    t={metrics.samples[-1][0]:6.2f}s  backlog={metrics.samples[-1][1]}")

        print("== 阶段 4：一致性核对 ==")
        total_received = len(transport.received_ids)
        dup = total_received - len(set(transport.received_ids))
        print(f"  服务端共收到 {total_received} 条，重复 {dup} 条（重复检测必须为零）")
        print(f"  账目核对：实时 {coll.sent_online} + 重发 {coll.resent} + 丢弃 {coll.dropped}"
              f" = {coll.sent_online + coll.resent + coll.dropped}（应等于采集总数 1000）")
        assert dup == 0, "出现重复上报！"
        assert coll.sent_online + coll.resent + coll.dropped == 1000
        assert max(transport.batch_sizes) <= BATCH
        print("  全部断言通过。")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
