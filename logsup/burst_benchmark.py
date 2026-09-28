"""突发场景量化：抑制前后写入量、抑制率、关键信息保留。

运行: python3 burst_benchmark.py
"""

from log_suppressor import LogSuppressor


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def run_scenario(name, messages_per_sec, seconds, make_message, interval=1.0, max_templates=10_000):
    clock = FakeClock()
    sup = LogSuppressor(interval=interval, max_templates=max_templates, time_fn=clock)

    raw_bytes = 0
    out_bytes = 0
    first_occurrences = 0
    summaries = 0
    step = 1.0 / messages_per_sec

    for sec in range(seconds):
        for i in range(messages_per_sec):
            clock.now = sec + i * step
            msg = make_message(sec, i)
            raw_bytes += len(msg) + 1
            for line in sup.process(msg):
                out_bytes += len(line) + 1
                if line.startswith("[SUPPRESSED]"):
                    summaries += 1
                else:
                    first_occurrences += 1
    for line in sup.flush():
        out_bytes += len(line) + 1
        summaries += 1

    total = messages_per_sec * seconds
    ratio = out_bytes / raw_bytes if raw_bytes else 0
    print(f"=== {name} ===")
    print(f"  输入: {total:,} 条 / {raw_bytes / 1e6:.2f} MB ({messages_per_sec:,} 条/秒 x {seconds}s)")
    print(f"  输出: {first_occurrences + summaries:,} 条 / {out_bytes / 1e6:.4f} MB")
    print(f"    - 全量放行条目: {first_occurrences:,} 条 (每个新模板首条 + 每窗口首条)")
    print(f"    - 汇总条目:     {summaries:,} 条 (每条含「过去 N 秒重复 M 次」+ 参数分布)")
    print(f"  抑制: {sup.suppressed_total:,} 条被归并, 写入量降为 {ratio:.4%}, 抑制率 {1 - ratio:.4%}")
    print(f"  内存: 模板状态峰值 {sup.peak_templates:,} / 上限 {max_templates:,}, LRU 淘汰 {sup.evicted:,} 次")
    print()
    return sup


def main():
    # 场景 1: 单条高频 —— 故障期间同一错误每秒 3 万条
    run_scenario(
        "场景1: 单条高频错误 (30,000 条/秒)",
        messages_per_sec=30_000, seconds=10,
        make_message=lambda sec, i: f'ERROR db connect 10.0.0.{i % 255}:5432 timeout after {30 + i % 5}s retry={i}',
    )

    # 场景 2: 海量不同模板 —— 每条都是新模式, 考验内存上界与新品放行
    sup2 = run_scenario(
        "场景2: 海量不同模板 (5,000 个新模板/秒)",
        messages_per_sec=5_000, seconds=10,
        make_message=lambda sec, i: f"ERROR module{sec * 5000 + i} crashed unexpectedly",
        max_templates=1_000,
    )
    assert sup2.tracked_templates <= 1_000, "模板状态越界!"

    # 场景 3: 混合 —— 3 个高频模板 + 偶发新模板
    def mixed(sec, i):
        if i % 10_000 == 0:
            return f"FATAL new failure mode{sec}-{i} detected"  # 偶发新模式
        kind = i % 3
        return [
            f"WARN gc pause {100 + i % 50}ms heap {i % 8}G",
            f"ERROR rpc to 172.16.0.{i % 100}:8080 latency {i % 900}ms",
            f"ERROR disk /dev/sd{chr(97 + i % 4)} ioerr sector {i}",
        ][kind]

    run_scenario(
        "场景3: 混合突发 (3 个高频模板 + 每秒 1 个新模式)",
        messages_per_sec=20_000, seconds=10,
        make_message=mixed,
    )


if __name__ == "__main__":
    main()
