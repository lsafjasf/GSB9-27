"""性能基准：上万条失败的聚类耗时。用法: python3 benchmark.py [条数]"""

from __future__ import annotations

import sys
import time

from datagen import generate_bulk
from failcluster import FailureRecord, cluster_failures


def main() -> int:
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    data = generate_bulk(total)
    records = [FailureRecord(id=d["id"], text=d["text"]) for d in data]
    start = time.perf_counter()
    clusters = cluster_failures(records)
    elapsed = time.perf_counter() - start
    print(f"输入 {len(records)} 条失败 -> {len(clusters)} 组")
    print(f"聚类耗时: {elapsed:.3f}s ({len(records) / elapsed:.0f} 条/s)")
    top = clusters[0]
    print(f"最大组规模: {top.size}, 代表样本: {top.representative.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
