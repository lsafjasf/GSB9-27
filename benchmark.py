#!/usr/bin/env python3
"""生成十万文件级合成镜像并测量归因分析耗时。"""

import json
import os
import resource
import sys
import tempfile
import time

from image_size_attribution import analyze, load_manifest


def generate(path, n_files=100_000, n_layers=50):
    """50 层：底层 6 万文件，后续每层新增/覆盖/删除，最终文件树约 10 万。"""
    layers = []
    base = [{"path": "/usr/lib/mod%05d/pkg/file%05d.py" % (i % 500, i),
             "size": 100 + (i * 37) % 9000, "digest": "sha256:%x" % (i % 80000)}
            for i in range(60_000)]
    layers.append({"id": "layer-0", "files": base})
    next_id = 60_000
    for li in range(1, n_layers):
        files = []
        # 每层覆盖 400 个已有路径（同路径反复覆盖）
        for j in range(400):
            victim = (li * 400 + j) % 60_000
            files.append({"path": "/usr/lib/mod%05d/pkg/file%05d.py"
                          % (victim % 500, victim), "size": 5000})
        # 每层删除 100 个路径
        for j in range(100):
            victim = (li * 700 + j) % 60_000
            files.append({"path": "/usr/lib/mod%05d/pkg/file%05d.py"
                          % (victim % 500, victim), "type": "whiteout"})
        # 每层新增约 800 个文件，使最终文件树达到 ~10 万
        for j in range(820):
            files.append({"path": "/app/data/shard%03d/f%07d.dat"
                          % (next_id % 200, next_id),
                          "size": 200 + (next_id * 13) % 4000})
            next_id += 1
        layers.append({"id": "layer-%d" % li, "files": files})
    manifest = {"image": "bench:100k", "layers": layers}
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(manifest, fp)
    return sum(len(l["files"]) for l in layers)


def main():
    tmp = tempfile.mkdtemp(prefix="imgbench")
    mpath = os.path.join(tmp, "manifest.json")
    t0 = time.perf_counter()
    n_entries = generate(mpath)
    t_gen = time.perf_counter() - t0
    size_mb = os.path.getsize(mpath) / 1e6

    t0 = time.perf_counter()
    with open(mpath, encoding="utf-8") as fp:
        manifest = load_manifest(fp)
    t_load = time.perf_counter() - t0

    t0 = time.perf_counter()
    report = analyze(manifest, top=20)
    t_analyze = time.perf_counter() - t0

    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print("清单条目总数 : %d (manifest %.1f MB, 生成耗时 %.2fs)" % (n_entries, size_mb, t_gen))
    print("最终文件数   : %d" % report["file_count"])
    print("层数         : %d" % report["layer_count"])
    print("镜像总大小   : %d B" % report["total_size"])
    print("解析耗时     : %.3f s" % t_load)
    print("归因分析耗时 : %.3f s" % t_analyze)
    print("总耗时       : %.3f s" % (t_load + t_analyze))
    print("峰值内存     : %.0f MB" % peak_mb)
    assert all(report["checks"].values())
    print("自洽校验     : 全部通过")


if __name__ == "__main__":
    sys.exit(main())
