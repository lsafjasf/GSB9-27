"""离线模式与降级回归测试。

覆盖：
  1. 正常在线：实时直发、缓冲为空、无丢弃；
  2. 短暂断网：少量积压、恢复后全部补发，零重复；
  3. 长时间断网：积压超过上界，DROP_OLDEST 丢弃计数正确、保留最新数据；
  4. 恢复后积压很大：令牌桶限速、批量上界、积压单调下降、速率达标；
  5. 进程重启：游标与丢弃计数持久化，未确认数据不丢、已确认不重发；
  6. 服务端部分接受：只确认被接受的部分，零重复零丢失；
  7. 边界：上界为 1、字节数上界、速率参数校验。

运行：在仓库根目录执行  python -m unittest tests.test_offline_mode -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector.buffer import DiskBuffer  # noqa: E402
from collector.clock import FakeClock  # noqa: E402
from collector.collector import Collector  # noqa: E402


class FakeTransport:
    """可切换在线/离线的假服务端，记录收到的 id 与每批大小。"""

    def __init__(self, online=True, accept_limit=None):
        self.online = online
        # 每次 send 最多接受多少条（模拟部分接受）；None 表示整批接受。
        self.accept_limit = accept_limit
        self.received_ids = []
        self.batch_sizes = []
        self.calls = 0

    def send(self, batch):
        self.calls += 1
        self.batch_sizes.append(len(batch))
        if not self.online:
            raise ConnectionError("network unreachable")
        if self.accept_limit is None:
            accepted = len(batch)
        else:
            accepted = min(self.accept_limit, len(batch))
        self.received_ids.extend(rec["id"] for rec in batch[:accepted])
        return accepted


def make_collector(tmpdir, online=True, max_records=10_000, resend_rate=100.0,
                   batch_size=10, max_bytes=None, accept_limit=None):
    clock = FakeClock()
    transport = FakeTransport(online=online, accept_limit=accept_limit)
    buf = DiskBuffer(tmpdir, max_records=max_records, max_bytes=max_bytes)
    coll = Collector(buf, transport, clock=clock, resend_rate=resend_rate,
                     batch_size=batch_size)
    return coll, transport, clock


class OfflineModeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="collector_test_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # 1. 正常在线 --------------------------------------------------------------
    def test_01_normal_online(self):
        coll, transport, _ = make_collector(self.tmpdir, online=True)
        for i in range(100):
            self.assertTrue(coll.emit({"v": i}))
        self.assertEqual(len(transport.received_ids), 100)
        self.assertEqual(coll.backlog, 0)
        self.assertEqual(coll.dropped, 0)
        self.assertEqual(sorted(transport.received_ids), list(range(1, 101)))
        # 重复检测为零
        self.assertEqual(len(transport.received_ids), len(set(transport.received_ids)))

    # 2. 短暂断网 --------------------------------------------------------------
    def test_02_short_outage_all_recovered(self):
        coll, transport, _ = make_collector(self.tmpdir, online=False)
        for i in range(20):
            self.assertFalse(coll.emit({"v": i}))
        self.assertEqual(coll.backlog, 20)
        self.assertEqual(coll.dropped, 0)

        transport.online = True
        metrics = coll.drain()
        self.assertEqual(coll.backlog, 0)
        self.assertEqual(metrics.sent, 20)
        self.assertEqual(sorted(transport.received_ids), list(range(1, 21)))
        # 重复检测为零
        self.assertEqual(len(transport.received_ids), len(set(transport.received_ids)))

    # 3. 长时间断网：上界丢弃 --------------------------------------------------
    def test_03_long_outage_drop_oldest(self):
        cap = 50
        coll, transport, _ = make_collector(self.tmpdir, online=False,
                                            max_records=cap, batch_size=10)
        for i in range(130):
            coll.emit({"v": i})
        # 130 - 50 = 80 被丢弃，缓冲恰好停在上界
        self.assertEqual(coll.dropped, 80)
        self.assertEqual(coll.backlog, cap)
        # DROP_OLDEST：存活的是最新的 50 条（id 81..130）
        queued = coll._buffer.read_batch(cap)
        self.assertEqual([r["id"] for r in queued], list(range(81, 131)))

        transport.online = True
        metrics = coll.drain()
        self.assertEqual(coll.backlog, 0)
        self.assertEqual(metrics.sent, cap)
        self.assertEqual(sorted(transport.received_ids), list(range(81, 131)))
        # 重复检测为零
        self.assertEqual(len(transport.received_ids), len(set(transport.received_ids)))
        # 端到端计数：实时+补发+丢弃 == 采集总数
        self.assertEqual(coll.sent_online + metrics.sent + coll.dropped, 130)

    # 4. 大积压恢复：限速 ------------------------------------------------------
    def test_04_large_backlog_rate_limited(self):
        rate, batch = 100.0, 10
        coll, transport, clock = make_collector(self.tmpdir, online=False,
                                                resend_rate=rate, batch_size=batch)
        n = 300
        for i in range(n):
            coll.emit({"v": i})
        transport.online = True
        metrics = coll.drain()

        # 4a. 任何一次请求都不超过 batch_size —— 不会一股脑打满服务
        self.assertLessEqual(max(transport.batch_sizes), batch)
        # 4b. 初始突发之外，耗时必须与令牌桶限速一致（不超过持续速率）
        self.assertGreaterEqual(metrics.elapsed, (n - batch) / rate - 1e-9)
        # 4c. 实际重发速率落在合理区间（突发使速率略高于配置值）
        self.assertLessEqual(metrics.effective_rate, rate * 2.0)
        self.assertGreaterEqual(metrics.effective_rate, rate * 0.8)
        # 4d. 积压单调下降且最终清零
        pendings = [p for _, p in metrics.samples]
        self.assertEqual(pendings[-1], 0)
        self.assertTrue(all(b <= a for a, b in zip(pendings, pendings[1:])))
        # 4e. 零重复
        self.assertEqual(len(transport.received_ids), n)
        self.assertEqual(len(transport.received_ids), len(set(transport.received_ids)))

    # 5. 重启持久化 ------------------------------------------------------------
    def test_05_restart_persistence_and_no_duplicate(self):
        d = self.tmpdir
        c1, t1, _ = make_collector(d, online=False, max_records=10, batch_size=5)
        for i in range(5):
            c1.emit({"v": i})
        # 断网期间超出上界：丢弃计数应持久化
        for i in range(8):
            c1.emit({"v": i})
        self.assertEqual(c1.dropped, 3)
        del c1, t1

        # 模拟进程重启：重新打开同一目录
        c2, t2, _ = make_collector(d, online=True, max_records=10, batch_size=5)
        self.assertEqual(c2.dropped, 3)          # 丢弃计数跨重启保留
        self.assertEqual(c2.backlog, 10)         # 未确认数据未丢失
        c2.drain()
        self.assertEqual(c2.backlog, 0)
        self.assertEqual(len(t2.received_ids), 10)
        self.assertEqual(len(t2.received_ids), len(set(t2.received_ids)))

        # 再次重启：缓冲已清空，不应重发任何历史数据
        c3, t3, _ = make_collector(d, online=True, max_records=10, batch_size=5)
        c3.drain()
        self.assertEqual(t3.received_ids, [])

    # 6. 服务端部分接受：零重复零丢失 ------------------------------------------
    def test_06_partial_accept_no_duplicate(self):
        coll, transport, _ = make_collector(self.tmpdir, online=False,
                                            batch_size=10, accept_limit=3)
        for i in range(25):
            coll.emit({"v": i})
        transport.online = True
        coll.drain()
        self.assertEqual(coll.backlog, 0)
        self.assertEqual(sorted(transport.received_ids), list(range(1, 26)))
        # 重复检测为零：每条恰好上报一次
        self.assertEqual(len(transport.received_ids), len(set(transport.received_ids)))

    # 7. 边界用例 --------------------------------------------------------------
    def test_07_boundary_capacity_one(self):
        coll, transport, _ = make_collector(self.tmpdir, online=False,
                                            max_records=1, batch_size=1)
        coll.emit({"v": 1})
        coll.emit({"v": 2})
        coll.emit({"v": 3})
        self.assertEqual(coll.dropped, 2)
        self.assertEqual(coll.backlog, 1)
        transport.online = True
        coll.drain()
        self.assertEqual(transport.received_ids, [3])  # 仅最新一条存活

    def test_08_byte_size_bound(self):
        buf = DiskBuffer(self.tmpdir, max_records=1_000_000, max_bytes=2048)
        for i in range(500):
            buf.append({"id": i + 1, "ts": 0, "payload": "x" * 64})
        self.assertLessEqual(os.path.getsize(os.path.join(self.tmpdir, "buffer.jsonl")),
                             2048 + 128)
        self.assertGreater(buf.dropped, 0)
        # 存活记录仍然是最新的一段，且全部可读、无损坏
        queued = list(buf.read_all_pending())
        self.assertTrue(all(set(r.keys()) == {"id", "ts", "payload"} for r in queued))

    def test_09_state_file_is_valid_json(self):
        coll, transport, _ = make_collector(self.tmpdir, online=False, max_records=2)
        coll.emit({"v": 1})
        coll.emit({"v": 2})
        coll.emit({"v": 3})
        with open(os.path.join(self.tmpdir, "state.json"), encoding="utf-8") as fh:
            st = json.load(fh)
        self.assertIn("start", st)
        self.assertIn("dropped", st)
        self.assertEqual(st["dropped"], 1)

    def test_10_invalid_construction(self):
        buf = DiskBuffer(self.tmpdir, max_records=10)
        with self.assertRaises(ValueError):
            Collector(buf, FakeTransport(), resend_rate=0)
        with self.assertRaises(ValueError):
            Collector(buf, FakeTransport(), batch_size=0)
        with self.assertRaises(ValueError):
            DiskBuffer(self.tmpdir + "_bad", max_records=0)

    # 混合场景：在线 -> 断网 -> 恢复 -> 再次断网，全周期零重复 ------------------
    def test_11_flaky_cycle(self):
        coll, transport, _ = make_collector(self.tmpdir, online=True,
                                            resend_rate=100, batch_size=5)
        for i in range(10):
            coll.emit({"v": i})
        transport.online = False
        for i in range(20):
            coll.emit({"v": i})
        self.assertEqual(coll.backlog, 20)
        # 断网期间 drain 不应丢数据
        m0 = coll.drain()
        self.assertEqual(m0.sent, 0)
        self.assertEqual(coll.backlog, 20)
        transport.online = True
        m1 = coll.drain()
        self.assertEqual(coll.backlog, 0)
        # 全部 30 条恰好一次
        self.assertEqual(sorted(transport.received_ids), list(range(1, 31)))
        self.assertEqual(len(transport.received_ids), len(set(transport.received_ids)))
        # 恢复后的新数据走实时通道
        self.assertTrue(coll.emit({"v": "fresh"}))
        self.assertIn(31, transport.received_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
