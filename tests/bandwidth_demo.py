"""Bandwidth comparison demo.

Replays identical operation sequences on a naive (always full GET) client
and a caching (If-None-Match + If-Modified-Since) client sharing one
origin. Counts are taken from serialized HTTP/1.1 bytes.

Run:  python3 tests/bandwidth_demo.py
Also writes BANDWIDTH_REPORT.md next to the project root.
"""

import os
import random

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from condcache import CachingClient, NaiveClient, OriginStore


KB = 1024


class Scenario:
    def __init__(self, name):
        self.name = name
        self.store = OriginStore()
        self.naive = NaiveClient(self.store)
        self.conditional = CachingClient(self.store)
        self.gets = 0
        self.mismatches = 0

    def put(self, key, body, metadata=None):
        self.store.put(key, body, metadata)

    def touch(self, key, metadata):
        self.store.touch(key, metadata)

    def get(self, key):
        before = self.conditional.stats.hits
        naive_body = self.naive.get(key)
        effective = self.conditional.get(key)
        assert naive_body == effective, "differential check failed"
        self.gets += 1
        if effective != naive_body:  # pragma: no cover - belt and braces
            self.mismatches += 1
        return self.conditional.stats.hits > before

    def row(self):
        n, c = self.naive.stats, self.conditional.stats
        saved = n.total_bytes - c.total_bytes
        return {
            "name": self.name,
            "requests": n.requests,
            "hits": c.hits,
            "misses": c.requests - c.hits,
            "naive_req": n.request_bytes,
            "cond_req": c.request_bytes,
            "naive_resp": n.response_bytes,
            "cond_resp": c.response_bytes,
            "naive_total": n.total_bytes,
            "cond_total": c.total_bytes,
            "saved": saved,
            "pct": (saved / n.total_bytes * 100.0) if n.total_bytes else 0.0,
        }


def scenario_a():
    """Stable resource, fetched repeatedly: 1 miss + 99 validation hits."""
    scn = Scenario("A: 稳定资源重复读取 (4 KiB x 100)")
    scn.put("page", b"p" * (4 * KB), {"etag-stable": "yes"})
    for _ in range(100):
        scn.get("page")
    return scn


def scenario_b():
    """Realistic mix: 10 keys, mostly reads, some writes / metadata touches."""
    scn = Scenario("B: 真实混合负载 (10 键, 160 读 / 16 写 / 8 元数据更新)")
    rng = random.Random(42)
    keys = [f"res{i}" for i in range(10)]
    sizes = [256, 512, 1 * KB, 2 * KB, 8 * KB]
    for i, key in enumerate(keys):
        scn.put(key, bytes([65 + i]) * rng.choice(sizes), {"rev": "0"})

    for step in range(184):
        key = keys[rng.randrange(len(keys))]
        if step % 11 == 0:
            scn.put(key, bytes([step % 256]) * rng.choice(sizes), {"rev": str(step)})
        elif step % 23 == 0:
            scn.touch(key, {"rev": f"meta{step}"})
        else:
            scn.get(key)
    return scn


def scenario_c():
    """High churn: content changes on half the operations."""
    scn = Scenario("C: 高频更新 (80 读 / 40 写)")
    rng = random.Random(7)
    key = "hot"
    scn.put(key, b"v0" * 512)
    for step in range(120):
        if step % 3 == 0:
            scn.put(key, f"v{step}".encode() * 512)
        else:
            scn.get(key)
    return scn


def fmt(n):
    return f"{n:,}"


def print_report(rows):
    print("=" * 78)
    print("条件请求 vs 朴素全量响应 — 带宽对比 (HTTP/1.1 线序字节)")
    print("=" * 78)
    header = (
        f"{'场景':<42}{'全量':>12}{'条件':>12}{'节省':>10}{'比例':>8}"
    )
    print(header)
    print("-" * 78)
    totals = dict.fromkeys(
        ("requests", "hits", "misses", "naive_total", "cond_total", "saved"), 0
    )
    for r in rows:
        print(
            f"{r['name']:<42}"
            f"{fmt(r['naive_total']):>12}"
            f"{fmt(r['cond_total']):>12}"
            f"{fmt(r['saved']):>10}"
            f"{r['pct']:>7.1f}%"
        )
        for k in totals:
            totals[k] += r[k]
    print("-" * 78)
    pct = totals["saved"] / totals["naive_total"] * 100
    print(
        f"{'合计':<42}"
        f"{fmt(totals['naive_total']):>12}"
        f"{fmt(totals['cond_total']):>12}"
        f"{fmt(totals['saved']):>10}"
        f"{pct:>7.1f}%"
    )
    print()
    print("明细:")
    for r in rows:
        hit = r["requests"] - r["hits"]
        print(f"  {r['name']}")
        print(
            f"    请求 {r['requests']}  304命中 {r['hits']}  全量 {r['misses']}"
            f"   请求头增量 {fmt(r['cond_req'] - r['naive_req'])} B"
        )
        print(
            f"    响应字节: 全量 {fmt(r['naive_resp'])} -> 条件 "
            f"{fmt(r['cond_resp'])}"
        )

    # Fixed reference figures for the 4 KiB stable resource.
    from condcache.wire import request_bytes, response_bytes
    from condcache.protocol import Request, handle_conditional

    store = OriginStore()
    store.put("page", b"p" * (4 * KB))
    full = handle_conditional(store, Request("page"))
    reval = handle_conditional(
        store,
        Request(
            "page",
            if_none_match=full.headers["ETag"],
            if_modified_since=full.headers["Last-Modified"],
        ),
    )
    conditional_request = len(
        request_bytes(
            "GET",
            "page",
            {
                "If-None-Match": full.headers["ETag"],
                "If-Modified-Since": full.headers["Last-Modified"],
            },
        )
    )
    print()
    print("单次请求参考值 (4 KiB 资源):")
    print(f"  朴素 GET 请求        : {len(request_bytes('GET', 'page'))} B")
    print(
        f"  条件 GET 请求        : {conditional_request} B"
    )
    print(f"  200 全量响应        : {len(response_bytes(full))} B")
    print(f"  304 未修改响应      : {len(response_bytes(reval))} B")
    return totals


def write_markdown(rows, path):
    lines = [
        "# 带宽节省数据",
        "",
        "所有数字按 HTTP/1.1 线序消息字节统计（含请求行/响应行、头部、响应体），",
        "由 `tests/bandwidth_demo.py` 生成。",
        "",
        "| 场景 | 请求数 | 304 命中 | 全量 | 朴素字节 | 条件字节 | 节省字节 | 节省比例 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    total = dict.fromkeys(
        ("requests", "hits", "misses", "naive_total", "cond_total", "saved"), 0
    )
    for r in rows:
        lines.append(
            f"| {r['name']} | {r['requests']} | {r['hits']} | {r['misses']} | "
            f"{r['naive_total']} | {r['cond_total']} | {r['saved']} | "
            f"{r['pct']:.1f}% |"
        )
        for k in total:
            total[k] += r[k]
    pct = total["saved"] / total["naive_total"] * 100
    lines.append(
        f"| **合计** | **{total['requests']}** | **{total['hits']}** | "
        f"**{total['misses']}** | **{total['naive_total']}** | "
        f"**{total['cond_total']}** | **{total['saved']}** | **{pct:.1f}%** |"
    )
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def main():
    scenarios = [scenario_a(), scenario_b(), scenario_c()]
    rows = [s.row() for s in scenarios]
    totals = print_report(rows)
    assert all(s.mismatches == 0 for s in scenarios)
    assert rows[0]["hits"] == 99
    assert totals["saved"] > 0
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    write_markdown(rows, os.path.join(root, "BANDWIDTH_REPORT.md"))
    print()
    print("报告已写入 BANDWIDTH_REPORT.md")


if __name__ == "__main__":
    main()
