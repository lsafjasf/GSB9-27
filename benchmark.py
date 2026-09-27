"""性能与内存基准：百万次查询耗时 + 清理前后内存对比。

运行：python3 benchmark.py
"""

import time

from revocation_list import RevocationList, Verdict

N_RECORDS = 200_000      # 撤销记录规模
N_QUERIES = 1_000_000    # 查询次数


def main():
    clock_t = [1_000_000.0]
    rl = RevocationList(path=None, clock=lambda: clock_t[0])

    # 一半记录已自然过期（待清理），一半仍存活
    rl.revoke_many([(f"expired-{i}", 500_000) for i in range(N_RECORDS // 2)])
    rl.revoke_many([(f"live-{i}", 9_999_999) for i in range(N_RECORDS // 2)])

    before = rl.memory_usage()
    removed = rl.cleanup()  # now=1_000_000，清掉 exp<=1e6 的记录
    after = rl.memory_usage()
    print(f"清理前: {before['records']:>8,} 条, 约 {before['approx_bytes']/1024/1024:.2f} MiB")
    print(f"清理后: {after['records']:>8,} 条, 约 {after['approx_bytes']/1024/1024:.2f} MiB"
          f"（删除 {removed:,} 条，释放 {(before['approx_bytes']-after['approx_bytes'])/1024/1024:.2f} MiB）")

    # 构造混合查询：1/3 命中已撤销，1/3 未撤销，1/3 记录过期无法判定
    probes = []
    for i in range(N_QUERIES):
        m = i % 3
        if m == 0:
            probes.append((f"live-{i % (N_RECORDS // 2)}", 9_999_999))
        elif m == 1:
            probes.append((f"never-{i}", 9_999_999))
        else:
            probes.append((f"expired-{i % (N_RECORDS // 2)}", 500_000))

    counts = {v: 0 for v in Verdict}
    start = time.perf_counter()
    for jti, exp in probes:
        counts[rl.check(jti, exp)] += 1
    elapsed = time.perf_counter() - start

    print(f"\n{N_QUERIES:,} 次查询耗时: {elapsed:.3f} s"
          f"（{N_QUERIES/elapsed:,.0f} 次/秒, 平均 {elapsed/N_QUERIES*1e6:.2f} µs/次）")
    print("判定分布: " + ", ".join(f"{v.value}={counts[v]:,}" for v in Verdict))


if __name__ == "__main__":
    main()
