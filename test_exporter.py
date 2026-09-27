"""断点续传与完整性校验测试（标准库 unittest）。"""

import json
import os
import tempfile
import unittest

from exporter import (Exporter, IntegrityError, ListSource, SimulatedInterrupt,
                      SourceChangedError)


class CountingSource(ListSource):
    """统计 read() 实际读出的记录数，用于断言续传不重复导出。"""

    def __init__(self, records):
        super().__init__(records)
        self.records_read = 0

    def read(self, start, end):
        for rec in super().read(start, end):
            self.records_read += 1
            yield rec


class ExportTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.out = os.path.join(self.dir.name, "out.txt")
        self.progress = self.out + ".progress.json"

    def make_records(self, n):
        return [f"rec-{i:06d}-payload" for i in range(n)]

    def read_out_lines(self):
        with open(self.out, "r", encoding="utf-8") as f:
            return [line.rstrip("\n") for line in f]

    def load_progress(self):
        with open(self.progress, "r", encoding="utf-8") as f:
            return json.load(f)

    # -- 空数据集 -----------------------------------------------------------

    def test_empty_dataset(self):
        src = CountingSource([])
        stats = Exporter(src, self.out, chunk_size=100).export()
        self.assertEqual(stats["status"], "completed")
        self.assertEqual(stats["chunks_written"], 0)
        self.assertTrue(stats["verified"])
        self.assertEqual(self.read_out_lines(), [])
        prog = self.load_progress()
        self.assertTrue(prog["completed"])
        self.assertEqual(prog["chunks"], [])
        report = Exporter(src, self.out, chunk_size=100).verify()
        self.assertEqual(report["records"], 0)

    # -- 单块（数据量小于块大小） -------------------------------------------

    def test_single_chunk(self):
        records = self.make_records(10)
        src = CountingSource(records)
        stats = Exporter(src, self.out, chunk_size=1000).export()
        self.assertEqual(stats["chunks_written"], 1)
        self.assertEqual(src.records_read, 10)
        self.assertEqual(self.read_out_lines(), records)
        prog = self.load_progress()
        self.assertEqual(len(prog["chunks"]), 1)
        self.assertEqual((prog["chunks"][0]["start"], prog["chunks"][0]["end"]), (0, 10))

    # -- 恰好中断在块边界 -----------------------------------------------------

    def test_interrupt_exactly_at_chunk_boundary(self):
        records = self.make_records(1000)
        src = CountingSource(records)
        # 300 条 = 恰好 3 个完整块，中断发生在块边界
        with self.assertRaises(SimulatedInterrupt):
            Exporter(src, self.out, chunk_size=100).export(fail_after_records=300)
        self.assertEqual(len(self.load_progress()["chunks"]), 3)

        src2 = CountingSource(records)
        stats = Exporter(src2, self.out, chunk_size=100).export()
        self.assertEqual(stats["status"], "resumed")
        # 续传只读剩余 700 条，不重复导出已完成的 3 块
        self.assertEqual(src2.records_read, 700)
        self.assertEqual(stats["records_written"], 700)
        self.assertEqual(self.read_out_lines(), records)  # 无重复、无缺失
        self.assertTrue(stats["verified"])

    # -- 中断在块中间 ---------------------------------------------------------

    def test_interrupt_mid_chunk(self):
        records = self.make_records(1000)
        src = CountingSource(records)
        # 350 条：3 个完整块 + 第 4 块写了一半
        with self.assertRaises(SimulatedInterrupt):
            Exporter(src, self.out, chunk_size=100).export(fail_after_records=350)
        prog = self.load_progress()
        self.assertEqual(len(prog["chunks"]), 3)  # 半个块不进进度
        # 输出文件里确实残留了半个块的字节
        committed = sum(c["length"] for c in prog["chunks"])
        self.assertGreater(os.path.getsize(self.out), committed)

        src2 = CountingSource(records)
        stats = Exporter(src2, self.out, chunk_size=100).export()
        self.assertEqual(stats["status"], "resumed")
        # 续传从第 300 条开始（半截块被截断重写），只读 700 条
        self.assertEqual(src2.records_read, 700)
        self.assertEqual(self.read_out_lines(), records)
        self.assertTrue(stats["verified"])

    # -- 源数据被修改后拒绝续传 ------------------------------------------------

    def test_source_modified_refuses_resume(self):
        records = self.make_records(1000)
        src = CountingSource(records)
        with self.assertRaises(SimulatedInterrupt):
            Exporter(src, self.out, chunk_size=100).export(fail_after_records=300)

        # 情形 1：改了一条记录的内容（条数不变）
        modified = self.make_records(1000)
        modified[500] = "tampered-record"
        with self.assertRaises(SourceChangedError) as ctx:
            Exporter(CountingSource(modified), self.out, chunk_size=100).export()
        self.assertIn("拒绝续传", str(ctx.exception))
        self.assertIn("指纹变化", str(ctx.exception))

        # 情形 2：条数变化
        with self.assertRaises(SourceChangedError) as ctx:
            Exporter(CountingSource(self.make_records(1001)), self.out,
                     chunk_size=100).export()
        self.assertIn("条数变化", str(ctx.exception))

        # 进度文件保持原样，未被污染
        self.assertEqual(len(self.load_progress()["chunks"]), 3)

    # -- 块大小配置变化也拒绝续传 ----------------------------------------------

    def test_chunk_size_change_refuses_resume(self):
        records = self.make_records(1000)
        with self.assertRaises(SimulatedInterrupt):
            Exporter(ListSource(records), self.out, chunk_size=100).export(
                fail_after_records=300)
        with self.assertRaises(SourceChangedError) as ctx:
            Exporter(ListSource(records), self.out, chunk_size=200).export()
        self.assertIn("块大小", str(ctx.exception))

    # -- 已完成导出不会重复导出 --------------------------------------------------

    def test_completed_export_is_not_reexported(self):
        records = self.make_records(500)
        Exporter(ListSource(records), self.out, chunk_size=100).export()
        src2 = CountingSource(records)
        stats = Exporter(src2, self.out, chunk_size=100).export()
        self.assertEqual(stats["status"], "already_completed")
        self.assertEqual(src2.records_read, 0)

    # -- 完整性校验：定位损坏块 --------------------------------------------------

    def test_verify_locates_corrupted_chunk(self):
        records = self.make_records(1000)
        Exporter(ListSource(records), self.out, chunk_size=100).export()
        prog = self.load_progress()
        victim = prog["chunks"][4]  # 第 5 块，记录范围 [400, 500)
        with open(self.out, "r+b") as f:
            f.seek(victim["offset"] + 3)
            f.write(b"X")
        with self.assertRaises(IntegrityError) as ctx:
            Exporter(ListSource(records), self.out, chunk_size=100).verify()
        problems = ctx.exception.problems
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["index"], 4)
        self.assertEqual((problems[0]["start"], problems[0]["end"]), (400, 500))

    # -- 完整性校验：定位缺失块（文件尾部被截断） --------------------------------

    def test_verify_locates_missing_tail_chunks(self):
        records = self.make_records(1000)
        Exporter(ListSource(records), self.out, chunk_size=100).export()
        prog = self.load_progress()
        # 截掉最后两块的字节，模拟块丢失
        keep = prog["chunks"][7]["offset"] + prog["chunks"][7]["length"]
        with open(self.out, "r+b") as f:
            f.truncate(keep)
        with self.assertRaises(IntegrityError) as ctx:
            Exporter(ListSource(records), self.out, chunk_size=100).verify()
        bad = {p["index"] for p in ctx.exception.problems}
        self.assertIn(8, bad)
        self.assertIn(9, bad)

    # -- 进度文件损坏/丢失时的行为 ------------------------------------------------

    def test_verify_without_progress_file(self):
        with self.assertRaises(IntegrityError):
            Exporter(ListSource([]), self.out, chunk_size=100).verify()

    def test_output_missing_but_progress_exists(self):
        from exporter import ExportError
        records = self.make_records(500)
        with self.assertRaises(SimulatedInterrupt):
            Exporter(ListSource(records), self.out, chunk_size=100).export(
                fail_after_records=200)
        os.remove(self.out)
        with self.assertRaises(ExportError) as ctx:
            Exporter(ListSource(records), self.out, chunk_size=100).export()
        self.assertIn("无法续传", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
