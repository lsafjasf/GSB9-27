"""rotlog 自测：不丢行断言、保留/清理统计、异常与边界情形。"""

import gzip
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rotlog import RotatingLogWriter


def read_all_lines(directory, base):
    """按时间顺序读出 活动文件+全部旧文件 的所有行（旧文件按名字升序）。"""
    names = sorted(
        n for n in os.listdir(directory) if n.startswith(base + ".")
    ) + [base]
    lines = []
    for name in names:
        path = os.path.join(directory, name)
        if not os.path.exists(path):
            continue
        if name.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                lines.extend(f.read().splitlines())
        else:
            with open(path, "r", encoding="utf-8") as f:
                lines.extend(f.read().splitlines())
    return lines


def backup_names(directory, base):
    return sorted(n for n in os.listdir(directory) if n.startswith(base + "."))


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class RotLogTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rotlog-test-")
        self.path = os.path.join(self.dir, "app.log")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make(self, **kw):
        kw.setdefault("compress", True)
        return RotatingLogWriter(self.path, **kw)

    # ---------------------------------------------------------- 不丢行断言

    def test_size_rotation_no_line_loss(self):
        n = 5000
        w = self.make(max_bytes=32 * 1024)
        for i in range(n):
            w.write_line(f"line-{i:06d}")
        w.close()
        lines = read_all_lines(self.dir, "app.log")
        self.assertEqual(lines, [f"line-{i:06d}" for i in range(n)])
        self.assertGreaterEqual(w.stats.rotations, 1)

    def test_lines_never_split_across_files(self):
        w = self.make(max_bytes=100, compress=False)
        for i in range(50):
            w.write_line(f"rec-{i:03d}-payload")
        w.close()
        for name in os.listdir(self.dir):
            with open(os.path.join(self.dir, name), "rb") as f:
                data = f.read()
            if not data:
                continue
            self.assertTrue(data.endswith(b"\n"), f"{name} 末尾出现半行")
            for raw in data.split(b"\n"):
                if raw:
                    self.assertTrue(raw.startswith(b"rec-"), f"{name} 开头出现半行")

    def test_concurrent_writers_no_loss(self):
        threads, per = 8, 4000
        w = self.make(max_bytes=256 * 1024)

        def worker(tid):
            for i in range(per):
                w.write_line(f"t{tid}-{i:06d}")

        ts = [threading.Thread(target=worker, args=(t,)) for t in range(threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        w.close()
        lines = read_all_lines(self.dir, "app.log")
        self.assertEqual(len(lines), threads * per)  # 一行不丢
        self.assertEqual(len(set(lines)), threads * per)  # 无重复
        for ln in lines:  # 每行完整
            tid, seq = ln.split("-")
            self.assertTrue(tid.startswith("t") and seq.isdigit())

    # ---------------------------------------------------------- 触发条件

    def test_time_rotation(self):
        w = self.make(interval=0.15)
        for _ in range(6):
            w.write_line("tick")
            time.sleep(0.08)
        w.close()
        self.assertGreaterEqual(w.stats.rotations, 2)
        self.assertEqual(len(read_all_lines(self.dir, "app.log")), 6)

    def test_size_and_time_together(self):
        clock = FakeClock()
        w = self.make(max_bytes=200, interval=100, time_fn=clock)
        for i in range(30):  # 触发大小轮转
            w.write_line(f"size-{i:03d}-xxxxxxxxxx")
        size_rotations = w.stats.rotations
        self.assertGreater(size_rotations, 0)
        clock.t += 200  # 触发时间轮转
        w.write_line("after-time-jump")
        self.assertGreater(w.stats.rotations, size_rotations)
        w.close()

    # ---------------------------------------------------------- 边界情形

    def test_single_line_exceeds_threshold(self):
        big = "X" * 5000
        w = self.make(max_bytes=100)
        w.write_line("before")
        w.write_line(big)
        w.write_line("after")
        w.close()
        lines = read_all_lines(self.dir, "app.log")
        self.assertEqual(lines, ["before", big, "after"])  # 超长行完整保留
        self.assertGreaterEqual(w.stats.rotations, 2)

    def test_rotation_failure_readonly_fs(self):
        if os.geteuid() == 0:
            self.skipTest("root 下只读权限不生效")
        w = self.make(max_bytes=100, retry_interval=0.01)
        w.write_line("seed")
        os.chmod(self.dir, 0o555)  # 目录只读：无法 rename/创建新文件
        try:
            for i in range(50):
                w.write_line(f"line-{i:03d}-yyyyyyyyyy")
            w.flush()
            self.assertGreater(w.stats.rotate_errors, 0)  # 记录了失败
            self.assertIsNotNone(w.stats.last_error)
        finally:
            os.chmod(self.dir, 0o755)
            w.close()
        # 所有行都在活动文件里，一行未丢
        with open(self.path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 51)
        self.assertEqual(lines[0], "seed")

    def test_time_rollback_no_burst_rotation(self):
        clock = FakeClock()
        w = self.make(interval=100, time_fn=clock)
        w.write_line("a")
        clock.t += 150
        w.write_line("b")  # 正常时间轮转
        self.assertEqual(w.stats.rotations, 1)
        clock.t -= 1000  # 时间回拨
        for i in range(20):
            w.write_line(f"rb-{i}")
            clock.t += 10  # 回拨后缓慢前进，仍远未到 next_rotate_time
        self.assertEqual(w.stats.rotations, 1)  # 没有补偿性连续轮转
        self.assertGreater(w.stats.time_rollbacks, 0)
        clock.t += 10_000  # 时钟恢复并越过阈值
        w.write_line("c")
        self.assertEqual(w.stats.rotations, 2)
        w.close()
        self.assertEqual(len(read_all_lines(self.dir, "app.log")), 23)

    def test_rapid_consecutive_rotations(self):
        clock = FakeClock()
        w = self.make(max_bytes=60, interval=5, time_fn=clock)
        n = 300
        for i in range(n):
            clock.t += 10  # 大小+时间同时命中，连续快速轮转
            w.write_line(f"rapid-{i:04d}-zzzzzzzzzz")
        w.close()
        self.assertGreater(w.stats.rotations, 100)
        lines = read_all_lines(self.dir, "app.log")
        self.assertEqual(lines, [f"rapid-{i:04d}-zzzzzzzzzz" for i in range(n)])
        # 文件名无冲突
        self.assertEqual(
            len(backup_names(self.dir, "app.log")),
            len(set(backup_names(self.dir, "app.log"))),
        )

    # ---------------------------------------------------------- 保留与清理

    def test_retention_by_count(self):
        w = self.make(max_bytes=60, max_backups=3)
        for i in range(200):
            w.write_line(f"c-{i:04d}-zzzzzzzzzz")
        w.close()
        backups = backup_names(self.dir, "app.log")
        self.assertEqual(len(backups), 3)
        st = w.stats.last_cleanup
        self.assertGreater(st.deleted_files, 0)
        self.assertEqual(st.remaining_files, 3)
        self.assertGreater(st.freed_bytes, 0)
        self.assertTrue(os.path.exists(self.path))  # 活动文件未被删

    def test_retention_by_total_bytes(self):
        w = self.make(max_bytes=200, max_total_bytes=600, compress=False)
        for i in range(200):
            w.write_line(f"s-{i:04d}-zzzzzzzzzzzzzzzzzzzz")
        w.close()
        total = sum(
            os.path.getsize(os.path.join(self.dir, n))
            for n in backup_names(self.dir, "app.log")
        )
        self.assertLessEqual(total, 600)
        self.assertGreater(w.stats.last_cleanup.deleted_files, 0)
        self.assertTrue(os.path.exists(self.path))

    def test_retention_count_and_total_together(self):
        w = self.make(max_bytes=100, max_backups=10, max_total_bytes=300,
                      compress=False)
        for i in range(300):
            w.write_line(f"b-{i:04d}-zzzzzzzzzzzzzzzz")
        w.close()
        backups = backup_names(self.dir, "app.log")
        self.assertLessEqual(len(backups), 10)
        total = sum(os.path.getsize(os.path.join(self.dir, n)) for n in backups)
        self.assertLessEqual(total, 300)
        st = w.stats.last_cleanup
        self.assertEqual(st.remaining_files, len(backups))
        self.assertEqual(st.remaining_bytes, total)

    # ---------------------------------------------------------- 压缩

    def test_compression_content(self):
        w = self.make(max_bytes=50, compress=True)
        for i in range(20):
            w.write_line(f"gz-{i:03d}-pppppppppp")
        w.close()
        gz_files = [n for n in backup_names(self.dir, "app.log") if n.endswith(".gz")]
        self.assertEqual(gz_files, backup_names(self.dir, "app.log"))  # 全部压缩
        content = []
        for n in gz_files:
            with gzip.open(os.path.join(self.dir, n), "rt") as f:
                content.extend(f.read().splitlines())
        self.assertTrue(content[0].startswith("gz-000"))

    def test_stats_reported(self):
        w = self.make(max_bytes=100, max_backups=2)
        for i in range(100):
            w.write_line(f"st-{i:04d}-qqqqqqqqqq")
        w.close()
        s = w.stats
        self.assertEqual(s.lines_written, 100)
        self.assertGreater(s.rotations, 2)
        self.assertGreater(s.last_cleanup.deleted_files, 0)
        self.assertEqual(s.last_cleanup.remaining_files, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
