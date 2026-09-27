"""断点续传分块导出工具（仅标准库）。

核心能力：
- 分块导出，每块写盘并 fsync 后原子更新进度文件（范围 + 字节偏移 + SHA-256）。
- 中断后续传：从上次已完成块的下一位置继续，不重复导出已完成块；
  上次中断留下的半个块会被截断重写。
- 续传前校验源快照（条数 + 全量内容指纹），源数据变化时拒绝续传，
  避免把两份不同数据混进同一个输出文件。
- 导出完成后做整体完整性校验（总条数 + 每块校验值），缺失/损坏块可定位。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

PROGRESS_VERSION = 1
DEFAULT_CHUNK_SIZE = 10000


class ExportError(Exception):
    """导出过程中的通用错误。"""


class SourceChangedError(ExportError):
    """源数据快照与进度文件不一致，拒绝续传。"""


class IntegrityError(ExportError):
    """完整性校验失败；problems 中列出每个缺失/损坏块的定位信息。"""

    def __init__(self, message, problems=None):
        super().__init__(message)
        self.problems = problems or []


class SimulatedInterrupt(Exception):
    """模拟进程中断（断电/被杀），仅供测试与基准使用。"""


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------

class ListSource:
    """内存记录列表数据源。records 中的记录会被 serializer 转成一行文本。"""

    def __init__(self, records):
        self.records = list(records)

    def __len__(self):
        return len(self.records)

    def fingerprint(self):
        h = hashlib.sha256()
        h.update(b"list-v1")
        h.update(str(len(self.records)).encode())
        for rec in self.records:
            data = repr(rec).encode("utf-8")
            h.update(str(len(data)).encode())
            h.update(b":")
            h.update(data)
        return h.hexdigest()

    def read(self, start, end):
        return iter(self.records[start:end])


class TextFileSource:
    """文本文件数据源，每行一条记录。指纹 = 文件内容哈希 + 行数。"""

    def __init__(self, path):
        self.path = path

    def __len__(self):
        n = 0
        with open(self.path, "rb") as f:
            for _ in f:
                n += 1
        return n

    def fingerprint(self):
        h = hashlib.sha256()
        h.update(b"file-v1")
        with open(self.path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        h.update(str(len(self)).encode())
        return h.hexdigest()

    def read(self, start, end):
        def gen():
            with open(self.path, "r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i >= end:
                        break
                    if i >= start:
                        yield line.rstrip("\n")
        return gen()


class SyntheticSource:
    """合成数据源，用于演示与基准测试；指纹只依赖 (count, width)，O(1)。"""

    def __init__(self, count, width=64):
        self.count = count
        self.width = width

    def __len__(self):
        return self.count

    def fingerprint(self):
        h = hashlib.sha256()
        h.update(b"synthetic-v1")
        h.update(f"{self.count}:{self.width}".encode())
        return h.hexdigest()

    def read(self, start, end):
        for i in range(start, min(end, self.count)):
            yield f"record-{i:012d}-{'x' * self.width}"


# ---------------------------------------------------------------------------
# 导出器
# ---------------------------------------------------------------------------

def _default_serializer(record):
    return str(record)


class Exporter:
    """分块导出器。进度文件为 <out_path>.progress.json。"""

    def __init__(self, source, out_path, chunk_size=DEFAULT_CHUNK_SIZE,
                 serializer=None, sleep_per_chunk=0.0):
        if chunk_size <= 0:
            raise ValueError("chunk_size 必须为正整数")
        self.source = source
        self.out_path = out_path
        self.chunk_size = chunk_size
        self.serializer = serializer or _default_serializer
        self.sleep_per_chunk = sleep_per_chunk  # 演示/基准用：模拟每块 I/O 耗时
        self.progress_path = out_path + ".progress.json"

    # -- 进度文件 ----------------------------------------------------------

    def _load_progress(self):
        if not os.path.exists(self.progress_path):
            return None
        with open(self.progress_path, "r", encoding="utf-8") as f:
            prog = json.load(f)
        if prog.get("version") != PROGRESS_VERSION:
            raise ExportError(f"进度文件版本不支持: {prog.get('version')}")
        return prog

    def _save_progress(self, prog):
        tmp = self.progress_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(prog, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.progress_path)  # 原子替换，崩溃不会留下半个进度文件

    def _new_progress(self, fingerprint, count):
        return {
            "version": PROGRESS_VERSION,
            "source_fingerprint": fingerprint,
            "source_count": count,
            "chunk_size": self.chunk_size,
            "chunks": [],
            "completed": False,
        }

    # -- 导出 --------------------------------------------------------------

    def export(self, fail_after_records=None):
        """执行导出；若存在未完成进度则续传。

        fail_after_records: 仅测试/基准用，本次运行写出 N 条记录后模拟崩溃
        （可能留下写了一半的块，且该块不会记入进度）。
        返回统计信息 dict。
        """
        started = time.monotonic()
        fingerprint = self.source.fingerprint()
        count = len(self.source)

        prog = self._load_progress()
        if prog is not None:
            self._check_resumable(prog, fingerprint, count)
            if prog["completed"]:
                return {"status": "already_completed", "records_written": 0,
                        "chunks_written": 0, "elapsed": 0.0}
            done_records = prog["chunks"][-1]["end"] if prog["chunks"] else 0
            offset = (prog["chunks"][-1]["offset"] + prog["chunks"][-1]["length"]
                      if prog["chunks"] else 0)
            if not os.path.exists(self.out_path):
                raise ExportError(
                    f"进度文件存在但输出文件 {self.out_path} 丢失，无法续传；"
                    f"请同时删除 {self.progress_path} 后全量重跑。")
            # 截断到上一个已完成块的末尾，丢弃中断时写了一半的块
            out = open(self.out_path, "r+b")
            out.truncate(offset)
            out.seek(offset)
            resumed = True
        else:
            prog = self._new_progress(fingerprint, count)
            done_records = 0
            offset = 0
            out = open(self.out_path, "wb")
            resumed = False

        written = 0
        chunks_written = 0
        try:
            pos = done_records
            while pos < count:
                end = min(pos + self.chunk_size, count)
                chunk_sha = hashlib.sha256()
                chunk_len = 0
                for rec in self.source.read(pos, end):
                    if fail_after_records is not None and written >= fail_after_records:
                        out.flush()
                        os.fsync(out.fileno())
                        raise SimulatedInterrupt(
                            f"模拟中断：本次运行已写出 {written} 条"
                            f"（第 {len(prog['chunks'])} 块未完成）")
                    line = (self.serializer(rec) + "\n").encode("utf-8")
                    out.write(line)
                    chunk_sha.update(line)
                    chunk_len += len(line)
                    written += 1
                out.flush()
                os.fsync(out.fileno())
                prog["chunks"].append({
                    "index": len(prog["chunks"]),
                    "start": pos,
                    "end": end,
                    "count": end - pos,
                    "offset": offset,
                    "length": chunk_len,
                    "sha256": chunk_sha.hexdigest(),
                })
                offset += chunk_len
                self._save_progress(prog)  # 先 fsync 数据，再落进度
                chunks_written += 1
                pos = end
                if self.sleep_per_chunk:
                    time.sleep(self.sleep_per_chunk)
            prog["completed"] = True
            self._save_progress(prog)
        finally:
            out.close()

        report = self.verify()
        return {
            "status": "resumed" if resumed else "completed",
            "records_written": written,
            "chunks_written": chunks_written,
            "total_records": count,
            "elapsed": time.monotonic() - started,
            "verified": report["ok"],
        }

    def _check_resumable(self, prog, fingerprint, count):
        reasons = []
        if prog["source_fingerprint"] != fingerprint:
            reasons.append(
                f"源内容指纹变化（{prog['source_fingerprint'][:12]}… -> "
                f"{fingerprint[:12]}…）")
        if prog["source_count"] != count:
            reasons.append(f"源条数变化（{prog['source_count']} -> {count}）")
        if prog["chunk_size"] != self.chunk_size:
            reasons.append(f"块大小配置变化（{prog['chunk_size']} -> {self.chunk_size}）")
        if reasons:
            raise SourceChangedError(
                "拒绝续传：源数据快照已变化，继续导出会把两份不同数据混进同一文件。\n"
                "  - " + "\n  - ".join(reasons) + "\n"
                f"请删除 {self.out_path} 和 {self.progress_path} 后全量重跑。")

    # -- 完整性校验 ----------------------------------------------------------

    def verify(self):
        """校验输出文件整体完整性：覆盖连续性 + 总条数 + 每块 SHA-256。

        任何缺失/损坏块都会以 {index, start, end, reason} 形式定位。
        """
        prog = self._load_progress()
        if prog is None:
            raise IntegrityError(f"缺少进度文件 {self.progress_path}，无法校验")
        problems = []
        if not prog["completed"]:
            problems.append({"index": None, "start": None, "end": None,
                             "reason": "导出未完成（completed=false）"})

        # 覆盖连续性：块必须无缝覆盖 [0, source_count)
        pos = 0
        for chunk in prog["chunks"]:
            if chunk["start"] != pos:
                problems.append({"index": chunk["index"], "start": pos,
                                 "end": chunk["start"],
                                 "reason": f"记录范围 [{pos}, {chunk['start']}) 缺失"})
            pos = chunk["end"]
        if pos != prog["source_count"]:
            problems.append({"index": None, "start": pos, "end": prog["source_count"],
                             "reason": f"记录范围 [{pos}, {prog['source_count']}) 缺失"})

        # 每块字节级校验
        if os.path.exists(self.out_path):
            with open(self.out_path, "rb") as f:
                for chunk in prog["chunks"]:
                    f.seek(chunk["offset"])
                    data = f.read(chunk["length"])
                    if len(data) != chunk["length"]:
                        problems.append({
                            "index": chunk["index"], "start": chunk["start"],
                            "end": chunk["end"],
                            "reason": f"块数据截断（期望 {chunk['length']} 字节，"
                                      f"实际 {len(data)} 字节）"})
                    elif hashlib.sha256(data).hexdigest() != chunk["sha256"]:
                        problems.append({
                            "index": chunk["index"], "start": chunk["start"],
                            "end": chunk["end"], "reason": "块校验值不匹配（数据损坏）"})
        else:
            problems.append({"index": None, "start": None, "end": None,
                             "reason": f"输出文件 {self.out_path} 不存在"})

        total = sum(c["count"] for c in prog["chunks"])
        if total != prog["source_count"]:
            problems.append({"index": None, "start": None, "end": None,
                             "reason": f"总条数不符（{total} != {prog['source_count']}）"})

        if problems:
            raise IntegrityError(
                f"完整性校验失败，共 {len(problems)} 处问题："
                + "; ".join(f"块{p['index']} 范围[{p['start']},{p['end']}): {p['reason']}"
                            for p in problems[:5]),
                problems)
        return {"ok": True, "chunks": len(prog["chunks"]),
                "records": total,
                "bytes": os.path.getsize(self.out_path)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_source(args):
    if args.source_file:
        return TextFileSource(args.source_file)
    return SyntheticSource(args.records)


def main(argv=None):
    parser = argparse.ArgumentParser(description="断点续传分块导出工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export", help="导出（有进度则自动续传）")
    p_exp.add_argument("--out", required=True, help="输出文件路径")
    p_exp.add_argument("--source-file", help="源文本文件（每行一条记录）")
    p_exp.add_argument("--records", type=int, default=100000,
                       help="合成数据条数（未指定 --source-file 时生效）")
    p_exp.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    p_exp.add_argument("--sleep-per-chunk", type=float, default=0.0,
                       help="每块模拟 I/O 延时（秒），用于演示")
    p_exp.add_argument("--fail-after", type=int, default=None,
                       help="写出 N 条后模拟崩溃（测试用）")

    p_ver = sub.add_parser("verify", help="校验已有导出的完整性")
    p_ver.add_argument("--out", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "export":
        exporter = Exporter(_build_source(args), args.out,
                            chunk_size=args.chunk_size,
                            sleep_per_chunk=args.sleep_per_chunk)
        try:
            stats = exporter.export(fail_after_records=args.fail_after)
        except SimulatedInterrupt as e:
            print(f"[中断] {e}", file=sys.stderr)
            return 3
        except SourceChangedError as e:
            print(str(e), file=sys.stderr)
            return 2
        print(json.dumps(stats, ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "verify":
        exporter = Exporter(SyntheticSource(0), args.out)
        try:
            report = exporter.verify()
        except IntegrityError as e:
            print(str(e), file=sys.stderr)
            for p in e.problems:
                print(f"  块{p['index']} 范围[{p['start']},{p['end']}): {p['reason']}",
                      file=sys.stderr)
            return 1
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0


if __name__ == "__main__":
    sys.exit(main())
