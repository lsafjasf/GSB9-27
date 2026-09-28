"""metrics.py 回归测试（标准库 unittest）。

覆盖：正常重启、强杀(kill -9)、连续两次重启、落盘文件过期、
文件截断/篡改损坏处理，以及守恒与单调性断言。

运行：python3 -m unittest test_metrics -v
"""

import glob
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from metrics import CorruptStateError, MetricsStore

WINDOW = 3600
TTL = 7 * 24 * 3600


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class MetricsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "counter.state")
        self.clock = FakeClock()

    def open_store(self, **kw):
        kw.setdefault("clock", self.clock)
        kw.setdefault("window_seconds", WINDOW)
        kw.setdefault("state_ttl", TTL)
        return MetricsStore(self.path, **kw)

    # ---------- 守恒：重启点落在窗口中间 ----------

    def test_conservation_restart_mid_window(self):
        # 窗口 [1000000//3600*3600, +3600)，重启点在窗口中间
        base = (self.clock.t // WINDOW) * WINDOW
        self.clock.t = base + 100  # 窗口内 100s 处

        s1 = self.open_store()
        s1.incr(5)
        s1.flush()  # 模拟定期落盘后进程退出
        before_total = s1.total

        self.clock.t = base + 200  # 同一窗口内重启
        s2 = self.open_store()
        self.assertEqual(s2.recovered_from, "state")
        s2.incr(3)
        s2.flush()

        # 守恒断言：该窗口聚合值 == 重启前后之和
        self.assertEqual(s2.window_aggregate(base), 5 + 3)
        # 再重启一次读出来仍然守恒
        s3 = self.open_store()
        self.assertEqual(s3.window_aggregate(base), 8)
        self.assertEqual(s3.total, before_total + 3)

    # ---------- 单调性 ----------

    def test_monotonic_across_restarts(self):
        totals = []
        for i in range(4):
            s = self.open_store()
            s.incr(i + 1)
            s.flush()
            totals.append(s.total)
            self.clock.advance(10)
        # 单调性断言：重启序列中 total 从不回退
        self.assertEqual(totals, [1, 3, 6, 10])
        for prev, cur in zip(totals, totals[1:]):
            self.assertGreaterEqual(cur, prev, "重启后指标回退！")

    def test_negative_increment_rejected(self):
        s = self.open_store()
        with self.assertRaises(ValueError):
            s.incr(-1)

    # ---------- 正常重启（clean close） ----------

    def test_clean_restart(self):
        with self.open_store() as s:  # close() 自动 flush
            s.incr(7)
        s2 = self.open_store()
        self.assertEqual(s2.recovered_from, "state")
        self.assertEqual(s2.total, 7)

    # ---------- 强杀（kill -9，无清理机会） ----------

    def test_sigkill_recovery(self):
        child_code = (
            "import sys, time;"
            "sys.path.insert(0, {repo!r});"
            "from metrics import MetricsStore;"
            "s = MetricsStore({path!r});"
            "s.incr(42); s.flush();"           # 已落盘部分
            "print('READY', flush=True);"
            "s.incr(58);"                       # 未落盘，强杀后允许丢失
            "time.sleep(30)"
        ).format(repo=os.path.dirname(os.path.abspath(__file__)),
                 path=self.path)
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "READY")
            proc.send_signal(signal.SIGKILL)  # 强杀，不给 atexit 机会
            proc.wait(timeout=10)
            self.assertEqual(proc.returncode, -signal.SIGKILL)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()
            proc.stderr.close()

        s = self.open_store()
        # 强杀后恢复到最后一次原子落盘的值，且文件完整可校验
        self.assertEqual(s.recovered_from, "state")
        self.assertEqual(s.total, 42)
        # 单调性：重启续写不得回退
        s.incr(1)
        self.assertGreaterEqual(s.total, 42)

    # ---------- 连续两次重启 ----------

    def test_two_consecutive_restarts(self):
        base = (self.clock.t // WINDOW) * WINDOW
        self.clock.t = base + 10
        s1 = self.open_store(); s1.incr(2); s1.flush()
        self.clock.t = base + 20
        s2 = self.open_store(); s2.incr(3); s2.flush()
        self.clock.t = base + 30
        s3 = self.open_store(); s3.incr(4); s3.flush()

        s4 = self.open_store()
        self.assertEqual(s4.total, 2 + 3 + 4)
        self.assertEqual(s4.window_aggregate(base), 9)  # 同一窗口守恒

    # ---------- 落盘文件过期 ----------

    def test_expired_state_explicit_reset(self):
        s1 = self.open_store()
        s1.incr(100)
        s1.flush()

        self.clock.advance(TTL + 1)  # 超过有效期
        s2 = self.open_store()
        # 明确策略：归档旧文件 + 显式重置（可观测，非静默）
        self.assertEqual(s2.recovered_from, "expired-reset")
        self.assertEqual(s2.total, 0)
        self.assertFalse(os.path.exists(self.path))
        archives = glob.glob(self.path + ".expired.*")
        self.assertEqual(len(archives), 1, "过期状态应被归档保留")

    # ---------- 损坏处理 ----------

    def test_truncated_file_detected(self):
        s1 = self.open_store(); s1.incr(9); s1.flush()
        with open(self.path, "rb") as f:
            data = f.read()
        with open(self.path, "wb") as f:
            f.write(data[: len(data) // 2])  # 模拟截断

        with self.assertRaises(CorruptStateError):
            self.open_store()
        # 损坏文件被隔离，原路径不存在 -> 不会误用半个文件
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(len(glob.glob(self.path + ".corrupt.*")), 1)

    def test_tampered_payload_detected(self):
        s1 = self.open_store(); s1.incr(9); s1.flush()
        with open(self.path, "r") as f:
            envelope = json.load(f)
        envelope["payload"]["total"] = 999999  # 篡改但不改 crc
        with open(self.path, "w") as f:
            json.dump(envelope, f)
        with self.assertRaises(CorruptStateError):
            self.open_store()

    def test_corrupt_explicit_reset_policy(self):
        with open(self.path, "wb") as f:
            f.write(b"garbage-not-json")
        s = self.open_store(on_corrupt="reset")  # 显式选择重置
        self.assertEqual(s.recovered_from, "corrupt-reset")
        self.assertEqual(s.total, 0)
        self.assertEqual(len(glob.glob(self.path + ".corrupt.*")), 1)

    def test_no_silent_zero_on_corrupt(self):
        # 默认策略下损坏绝不静默从零开始
        with open(self.path, "wb") as f:
            f.write(b"\x00\x01\x02")
        try:
            self.open_store()
        except CorruptStateError:
            pass
        else:
            self.fail("损坏文件被静默接受（从零开始）了")


if __name__ == "__main__":
    unittest.main()
