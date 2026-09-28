"""纠删码自测：恢复能力穷举 + 随机损坏组合 + 边界情形 + 开销/耗时。

运行：python3 -m erasure.selftest   （在仓库根目录）
或：  python3 erasure/selftest.py
"""

from __future__ import annotations

import itertools
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import erasure_code as ec


def corrupt(block: bytes, rng: random.Random) -> bytes:
    """随机翻转 1 至若干字节，保证结果与原块不同（块非空时）。"""
    if len(block) == 0:
        return b"\x00"  # 零块被塞入垃圾字节，校验和必然失配
    bad = bytearray(block)
    for _ in range(rng.randint(1, max(1, len(bad) // 7) + 1)):
        pos = rng.randrange(len(bad))
        bad[pos] ^= rng.randint(1, 255)
    if bytes(bad) == block:
        bad[0] ^= 0xFF
    return bytes(bad)


def make_corrupted(enc: ec.Encoded, damaged: tuple, rng: random.Random):
    blocks = list(enc.blocks)
    for idx in damaged:
        if rng.random() < 0.5:
            blocks[idx] = None  # 整块丢失
        else:
            blocks[idx] = corrupt(enc.blocks[idx], rng)
    return blocks


def run_combo_suite(label, data, k, m, exhaustive_to, rng, trials_per_size=200):
    """对损坏块数 0..m：小集合穷举全部 C(k+m, t) 组合，大集合随机抽样。"""
    enc = ec.encode(data, k, m)
    n = k + m
    print(f"\n[{label}] 数据 {len(data)} 字节, k={k}(数据块) m={m}(校验块), "
          f"总块数 n={n}, 可容忍同时损坏上限 = {m} 块")

    totals_by_t = {}
    all_pass = True
    t_decode_total = 0.0
    for t in range(0, m + 1):
        combos = list(itertools.combinations(range(n), t))
        if len(combos) <= exhaustive_to:
            chosen = combos
            mode = "穷举全部组合"
        else:
            chosen = [tuple(rng.sample(range(n), t)) for _ in range(trials_per_size)]
            mode = f"随机抽样 {trials_per_size}/{len(combos)} 组合"

        # 按损坏类型统计：纯数据块 / 纯校验块 / 混合
        stats = {"data-only": 0, "parity-only": 0, "mixed": 0}
        ok = 0
        t0 = time.perf_counter()
        for combo in chosen:
            blocks = make_corrupted(enc, combo, rng)
            recovered = ec.decode(enc, blocks)
            assert recovered == data, f"组合 {combo} 恢复结果不一致"
            if combo:
                if all(i < k for i in combo):
                    stats["data-only"] += 1
                elif all(i >= k for i in combo):
                    stats["parity-only"] += 1
                else:
                    stats["mixed"] += 1
            ok += 1
        dt = time.perf_counter() - t0
        t_decode_total += dt
        totals_by_t[t] = (ok, len(chosen))
        all_pass &= ok == len(chosen)
        print(f"  损坏 {t} 块 ({mode}): {ok}/{len(chosen)} 恢复成功 | "
              f"仅数据块 {stats['data-only']}, 仅校验块 {stats['parity-only']}, "
              f"混合 {stats['mixed']} | 平均 {dt / max(1,len(chosen)) * 1000:.3f} ms/次")

    total_ok = sum(v[0] for v in totals_by_t.values())
    total_run = sum(v[1] for v in totals_by_t.values())
    print(f"  小计: {total_ok}/{total_run} 组合恢复成功，"
          f"成功率 {total_ok / total_run * 100:.2f}%")
    return all_pass, t_decode_total / max(1, total_run)


def run_over_limit(label, data, k, m, rng):
    """损坏 m+1 块必须明确报错。"""
    enc = ec.encode(data, k, m)
    n = k + m
    combos = list(itertools.combinations(range(n), m + 1))
    rng.shuffle(combos)
    checked = 0
    for combo in combos[:min(20, len(combos))]:
        blocks = make_corrupted(enc, combo, rng)
        try:
            ec.decode(enc, blocks)
        except ec.UnrecoverableError:
            checked += 1
        else:
            raise AssertionError(f"[{label}] 损坏 {m + 1} 块未报错: {combo}")
    print(f"[{label}] 损坏 {m + 1} 块（超上限）: "
          f"抽查 {checked} 组合均按预期抛出 UnrecoverableError")
    return checked


def benchmark(label, size, k, m, rng, rounds=5):
    data = rng.randbytes(size)
    t0 = time.perf_counter()
    for _ in range(rounds):
        enc = ec.encode(data, k, m)
    enc_time = (time.perf_counter() - t0) / rounds

    # 最坏情形：恰好 m 个块损坏，且尽量多的数据块损坏
    worst = tuple(range(min(m, k)))
    if m > k:
        worst += tuple(range(k, k + (m - k)))
    blocks = make_corrupted(enc, worst, rng)
    t0 = time.perf_counter()
    for _ in range(rounds):
        rec = ec.decode(enc, blocks)
    dec_time = (time.perf_counter() - t0) / rounds
    assert rec == data

    parity_bytes = m * enc.block_size
    print(f"  {label}: 原始 {size:>9,} B, 块 {enc.block_size:>8,} B/块, "
          f"编码 {enc_time * 1000:8.2f} ms, 恢复({len(worst)}块损坏) "
          f"{dec_time * 1000:8.2f} ms, 恢复吞吐 {size / dec_time / 1e6:6.1f} MB/s")
    return parity_bytes, dec_time


def main():
    rng = random.Random(20260929)
    print("=" * 78)
    print("分块纠删码自测 (GF(2^8) Reed-Solomon / Cauchy 矩阵, 纯 Python 标准库)")
    print("规则: k 个数据块 + m 个校验块, 任意 m 个块(数据/校验)同时损坏可恢复")
    print("=" * 78)

    # ---------- 边界情形 ----------
    print("\n--- 边界情形 ---")
    # 零长度
    enc0 = ec.encode(b"", 4, 2)
    assert enc0.block_size == 0
    assert ec.decode(enc0, list(enc0.blocks)) == b""
    blocks0 = list(enc0.blocks); blocks0[0] = b"\x00"; blocks0[5] = None
    assert ec.decode(enc0, blocks0) == b""  # 坏 2 = m，仍可恢复
    try:
        ec.decode(enc0, [b"\x00"] * 6)      # 坏 6 > m
        raise AssertionError("零长度超上限未报错")
    except ec.UnrecoverableError:
        pass
    print("零长度数据: 正常解码、m 块内损坏可恢复、超上限报错  OK")

    # 单块数据（数据落在第 1 块，其余为填充）
    enc1 = ec.encode(b"Z", 4, 2)
    assert ec.decode(enc1, list(enc1.blocks)) == b"Z"
    for combo in itertools.combinations(range(6), 2):
        blocks = make_corrupted(enc1, combo, rng)
        assert ec.decode(enc1, blocks) == b"Z", combo
    print("单块数据(1 字节, k=4 m=2): 全部 C(6,2)=15 种双块损坏组合恢复成功  OK")

    # k=1 m=1：最小配置，单校验块
    enc_s = ec.encode(b"abc", 1, 1)
    blocks = list(enc_s.blocks); blocks[0] = None
    assert ec.decode(enc_s, blocks) == b"abc"
    print("最小配置 k=1,m=1: 数据块丢失后由校验块恢复  OK")

    # 参数非法
    for bad in ((0, 1), (1, 0), (200, 200)):
        try:
            ec.coding_matrix(*bad)
            raise AssertionError(f"非法参数 {bad} 未报错")
        except ValueError:
            pass
    print("非法参数 (k<1 / m<1 / k+m>256): ValueError  OK")

    # ---------- 恢复能力：穷举 + 随机组合 ----------
    print("\n--- 恢复能力组合测试 ---")
    configs = [
        ("小数据", 1234, 4, 2),       # n=6,  全部组合数 <= 64? C(6,2)=15 等，全穷举
        ("中数据", 4096, 6, 3),       # n=9
        ("大数据", 200 * 1024, 8, 4),  # n=12，t 大时组合多 -> 随机抽样
    ]
    EXHAUSTIVE_TO = 300
    for label, size, k, m in configs:
        data = rng.randbytes(size)
        ok, avg_ms = run_combo_suite(label, data, k, m, EXHAUSTIVE_TO, rng)
        assert ok
        run_over_limit(label, data, k, m, rng)

    # 单独做一轮高损坏率随机组合统计（大数据、不同 k/m）
    print("\n--- 随机损坏组合汇总 (每档损坏块数 300 次) ---")
    grand_ok = grand_total = 0
    for k, m in [(10, 4), (12, 2), (5, 5)]:
        data = rng.randbytes(64 * 1024)
        enc = ec.encode(data, k, m)
        n = k + m
        for t in range(1, m + 1):
            for _ in range(300):
                damaged = tuple(rng.sample(range(n), t))
                blocks = make_corrupted(enc, damaged, rng)
                assert ec.decode(enc, blocks) == data
                grand_ok += 1
            grand_total += 300
    print(f"  (k,m)=(10,4)/(12,2)/(5,5) 各损坏档 1..m: "
          f"{grand_ok}/{grand_total} 成功，成功率 {grand_ok / grand_total * 100:.2f}%")

    # ---------- 冗余开销 ----------
    print("\n--- 冗余开销（校验块大小占原始数据比例）---")
    size = 1024 * 1024
    for k, m in [(4, 2), (6, 3), (8, 4), (10, 4), (12, 2), (5, 5)]:
        data = rng.randbytes(size)
        enc = ec.encode(data, k, m)
        ratio = m * enc.block_size / size
        print(f"  k={k:2d} m={m}: 校验 {m} x {enc.block_size:>8,} B = "
              f"{m * enc.block_size:>10,} B, 开销 {ratio * 100:6.2f}% "
              f"(理论 m/k = {m / k * 100:.1f}%)")

    # ---------- 耗时基准 ----------
    print("\n--- 耗时基准 (每点 5 次取平均, 随机数据 1MB) ---")
    for k, m in [(4, 2), (8, 4), (12, 4)]:
        benchmark(f"k={k:2d} m={m}", 1024 * 1024, k, m, rng)

    print("\n" + "=" * 78)
    print("全部测试通过。")
    print("=" * 78)


if __name__ == "__main__":
    main()
