"""断点续传 vs 全量重跑 耗时对比。

模拟一个带 I/O 延时的导出场景（每块 20ms），对比三种路径：
  A. 全量导出一次跑完
  B. 旧工具行为：跑到 50% 中断，丢弃成果从头重跑
  C. 本工具：跑到 50% 中断（落在块中间），断点续传
"""

import os
import tempfile
import time

from exporter import Exporter, ListSource, SimulatedInterrupt

RECORDS = 30000
CHUNK_SIZE = 500
SLEEP_PER_CHUNK = 0.02  # 模拟每块的 I/O 延时


def make_source():
    return ListSource([f"record-{i:08d}-{'v' * 48}" for i in range(RECORDS)])


def run_full(out):
    src = make_source()
    t0 = time.monotonic()
    stats = Exporter(src, out, chunk_size=CHUNK_SIZE,
                     sleep_per_chunk=SLEEP_PER_CHUNK).export()
    return time.monotonic() - t0, stats


def run_interrupted(out, fail_after):
    """跑到 fail_after 条后崩溃，返回已耗时。"""
    src = make_source()
    t0 = time.monotonic()
    try:
        Exporter(src, out, chunk_size=CHUNK_SIZE,
                 sleep_per_chunk=SLEEP_PER_CHUNK).export(
            fail_after_records=fail_after)
    except SimulatedInterrupt:
        pass
    return time.monotonic() - t0


def run_resumed(out):
    src = make_source()
    t0 = time.monotonic()
    stats = Exporter(src, out, chunk_size=CHUNK_SIZE,
                     sleep_per_chunk=SLEEP_PER_CHUNK).export()
    return time.monotonic() - t0, stats


def main():
    fail_after = RECORDS // 2 + CHUNK_SIZE // 2  # 50%，且落在块中间
    tmp = tempfile.mkdtemp(prefix="export-bench-")
    out_a = os.path.join(tmp, "a.out")
    out_b = os.path.join(tmp, "b.out")
    out_c = os.path.join(tmp, "c.out")

    # A：全量一次跑完
    t_full, stats_a = run_full(out_a)

    # B：旧工具行为 —— 中断后丢弃成果，从头重跑
    t_b_first = run_interrupted(out_b, fail_after)
    for p in (out_b, out_b + ".progress.json"):
        if os.path.exists(p):
            os.remove(p)
    t_b_rerun, _ = run_full(out_b)
    t_b_total = t_b_first + t_b_rerun

    # C：本工具 —— 中断（块中间）后续传
    t_c_first = run_interrupted(out_c, fail_after)
    t_c_resume, stats_c = run_resumed(out_c)
    t_c_total = t_c_first + t_c_resume

    assert stats_a["verified"] and stats_c["verified"]
    with open(out_a, "rb") as fa, open(out_c, "rb") as fc:
        assert fa.read() == fc.read(), "续传产物与全量产物不一致"

    print(f"数据集: {RECORDS} 条, 块大小 {CHUNK_SIZE}, 每块模拟 I/O {SLEEP_PER_CHUNK*1000:.0f}ms")
    print(f"中断点: 第 {fail_after} 条（{fail_after/RECORDS:.0%}，落在块中间）")
    print()
    print(f"{'方案':<28}{'耗时':>10}  {'相对全量':>8}  说明")
    print("-" * 78)
    print(f"{'A. 全量导出（无中断）':<28}{t_full:>8.2f}s  {1.0:>8.2f}x  "
          f"写出 {stats_a['records_written']} 条")
    print(f"{'B. 中断后从头重跑（旧行为）':<28}{t_b_total:>8.2f}s  {t_b_total/t_full:>8.2f}x  "
          f"前半程 {t_b_first:.2f}s 作废 + 重跑 {t_b_rerun:.2f}s")
    print(f"{'C. 中断后断点续传（本工具）':<28}{t_c_total:>8.2f}s  {t_c_total/t_full:>8.2f}x  "
          f"前半程 {t_c_first:.2f}s + 续传 {t_c_resume:.2f}s")
    print()
    print(f"续传段仅写出剩余 {stats_c['records_written']} 条"
          f"（含半截块重写与完成后的整体校验），"
          f"相比从头重跑节省 {t_b_rerun - t_c_resume:.2f}s"
          f"（{(t_b_rerun - t_c_resume)/t_b_rerun:.0%}）")
    print(f"续传产物与全量产物字节级一致: 是")

    for p in os.listdir(tmp):
        os.remove(os.path.join(tmp, p))
    os.rmdir(tmp)


if __name__ == "__main__":
    main()
