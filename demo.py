"""演示：时间分段劣化的双向链路上跑停等 ARQ，并输出注入统计。

运行：python3 demo.py [seed]
"""
import sys

from netem import LinkParams, Segment, format_stats
from reliable_protocol import build_transfer

SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 42
N = 200

# 时间分段：平静期 -> 劣化期（10% 丢包 + 乱序 + 重复 + 抖动 + 限速）-> 恢复期
CALM = LinkParams(loss_rate=0.01, delay=0.05, jitter=0.01, bandwidth=200_000)
HARSH = LinkParams(loss_rate=0.10, delay=0.05, jitter=0.03,
                   reorder_rate=0.10, reorder_delay=0.08,
                   duplicate_rate=0.05, bandwidth=100_000)
SEGMENTS = [Segment(0.0, CALM, end=3.0),
            Segment(3.0, HARSH, end=60.0),
            Segment(60.0, CALM)]

payloads = [f"chunk-{i:04d}-{'x' * 32}" for i in range(N)]
sim, sender, receiver, ch_ab, ch_ba = build_transfer(
    SEED, SEGMENTS, SEGMENTS, payloads, timeout=0.4, size=1000)

sender.start()
sim.run(until=600.0)

ok = sender.done and receiver.received == payloads
print(f"种子={SEED}  报文数={N}  仿真用时={sim.now:.2f}s  重传={sender.retransmissions}")
print(f"传输{'成功，数据完整按序交付' if ok else '失败！'}")
print()
print(format_stats(ch_ab.name, ch_ab.stats))
print()
print(format_stats(ch_ba.name, ch_ba.stats))
sys.exit(0 if ok else 1)
