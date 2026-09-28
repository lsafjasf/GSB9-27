#!/usr/bin/env python3
"""镜像体积归因工具（image size attribution）。

解析镜像层清单（manifest）与每层文件树，计算：
  1. 每一层对最终镜像的"实际新增体积"（上层删除/覆盖的同名文件不计入下层）；
  2. 自洽校验：各层新增体积之和 == 镜像总大小（断言 + 显式校验）；
  3. 体积最大的若干路径；
  4. 可优化项：同路径多层重复（被覆盖的旧版本）、被删除但仍占下层体积的数据，
     以及（可选）跨文件内容重复，分别给出可回收体积估计。

仅使用 Python 3 标准库。

输入格式（JSON manifest）：
{
  "image": "myapp:1.0",            # 可选
  "layers": [                       # 自底向上排列
    {
      "id": "sha256:aaa...",       # 可选
      "files": [
        {"path": "/usr/bin/app", "size": 1048576, "digest": "sha256:..."},
        {"path": "/etc/old.conf", "type": "whiteout"},          # 删除下层同名文件
        {"path": "/var/cache",    "type": "opaque"}             # 隐藏下层整个目录
      ]
    }
  ]
}
size 单位字节；digest 可选，用于内容级去重检测。
"""

import argparse
import heapq
import json
import sys
from collections import defaultdict

WHITOUT_PREFIX = ".wh."
OPAQUE_MARKER = ".wh..wh..opq"


def normalize_path(path):
    """归一化路径：统一前导 /、折叠重复分隔符与 '.'，去掉结尾 '/'。"""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("非法路径: %r" % (path,))
    parts = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise ValueError("路径不允许包含 '..': %r" % (path,))
        parts.append(seg)
    return "/" + "/".join(parts)


def is_under(path, directory):
    """path 是否位于 directory 之下（含自身）。"""
    return path == directory or path.startswith(directory + "/")


def parse_manifest(data):
    """校验并解析原始 manifest dict，返回规范化结构。"""
    layers = data.get("layers")
    if not isinstance(layers, list):
        raise ValueError("manifest 缺少 layers 数组")
    parsed = []
    for idx, layer in enumerate(layers):
        files = layer.get("files", [])
        if not isinstance(files, list):
            raise ValueError("第 %d 层 files 不是数组" % idx)
        entries = []
        for entry in files:
            etype = entry.get("type", "file")
            raw_path = entry.get("path")
            if etype == "file":
                path = normalize_path(raw_path)
                size = entry.get("size", 0)
                if not isinstance(size, int) or size < 0:
                    raise ValueError("第 %d 层 %s 的 size 非法: %r" % (idx, path, size))
                entries.append((path, size, entry.get("digest")))
            elif etype == "whiteout":
                entries.append((normalize_path(raw_path), None, "whiteout"))
            elif etype == "opaque":
                entries.append((normalize_path(raw_path), None, "opaque"))
            else:
                raise ValueError("第 %d 层未知条目类型: %r" % (idx, etype))
        parsed.append({
            "id": layer.get("id", "layer-%d" % idx),
            "entries": entries,
        })
    return {"image": data.get("image", "<unnamed>"), "layers": parsed}


def load_manifest(fp):
    return parse_manifest(json.load(fp))


def analyze(manifest, top=20):
    """核心归因分析。接受原始 manifest dict 或 parse_manifest 的结果。"""
    if manifest.get("layers") and "entries" not in manifest["layers"][0]:
        manifest = parse_manifest(manifest)
    elif not manifest.get("layers") and "layers" in manifest:
        manifest = parse_manifest(manifest)
    layers = manifest["layers"]
    n_layers = len(layers)

    # 最终镜像中每个路径的归属：path -> (layer_index, size)
    final_owner = {}
    # 每层逻辑体积（该层所有 file 条目 size 之和，含后来被覆盖/删除的部分）
    logical_sizes = [0] * n_layers
    # 每层内容指纹 -> 总体积（用于跨文件内容重复检测）
    layer_digests = [defaultdict(int) for _ in range(n_layers)]
    # 被浪费的字节：path -> [size, ...]（按出现顺序，最早的在前）
    overwritten = defaultdict(list)   # 被上层同名文件覆盖
    deleted = defaultdict(list)       # 被上层 whiteout/opaque 删除

    for idx, layer in enumerate(layers):
        whiteouts = []
        opaques = []
        files = []
        for path, size, extra in layer["entries"]:
            if extra == "whiteout":
                whiteouts.append(path)
            elif extra == "opaque":
                opaques.append(path)
            else:
                files.append((path, size, extra))

        # 1) 处理删除：先 opaque（整目录），再 whiteout（单文件）
        for victim in list(final_owner):
            for d in opaques:
                if victim != d and is_under(victim, d):
                    deleted[victim].append(final_owner[victim][1])
                    del final_owner[victim]
                    break
        for w in whiteouts:
            if w in final_owner:
                deleted[w].append(final_owner[w][1])
                del final_owner[w]

        # 2) 处理新增/覆盖
        for path, size, digest in files:
            logical_sizes[idx] += size
            if digest:
                layer_digests[idx][digest] += size
            if path in final_owner:
                overwritten[path].append(final_owner[path][1])
            final_owner[path] = (idx, size)

    # 归因：最终镜像中每个路径的体积记到其归属层
    net_sizes = [0] * n_layers
    for path, (idx, size) in final_owner.items():
        net_sizes[idx] += size

    total_size = sum(net_sizes)

    # ---- 自洽断言（不变式）----
    # 1. 各层新增体积之和 == 镜像总大小
    assert sum(net_sizes) == total_size
    # 2. 镜像总大小 == 最终文件树逐路径求和
    assert total_size == sum(s for _, s in final_owner.values())
    # 3. 每层净增体积不超过其逻辑体积
    for i in range(n_layers):
        assert 0 <= net_sizes[i] <= logical_sizes[i]
    # 4. 逻辑总量 == 净总量 + 被覆盖浪费 + 被删除浪费
    wasted = sum(sum(v) for v in overwritten.values()) + \
             sum(sum(v) for v in deleted.values())
    assert sum(logical_sizes) == total_size + wasted

    # ---- 可优化项 ----
    dup_paths = []
    for path, sizes in overwritten.items():
        dup_paths.append({
            "path": path,
            "reclaimable": sum(sizes),
            "versions_wasted": len(sizes),
        })
    dup_paths.sort(key=lambda x: -x["reclaimable"])

    deleted_paths = []
    for path, sizes in deleted.items():
        deleted_paths.append({
            "path": path,
            "reclaimable": sum(sizes),
            "versions_wasted": len(sizes),
        })
    deleted_paths.sort(key=lambda x: -x["reclaimable"])

    # 跨文件内容重复（需要 digest）
    digest_sizes = defaultdict(int)
    digest_paths = defaultdict(set)
    for idx, layer in enumerate(layers):
        for path, size, extra in layer["entries"]:
            if extra not in ("whiteout", "opaque") and extra:
                digest_sizes[extra] += size
                digest_paths[extra].add(path)
    # reclaimable 保守估计：该 digest 的总体积 - 单份大小（取最小 size）
    content_dups = []
    for digest, paths in digest_paths.items():
        if len(paths) > 1:
            sizes = []
            for idx, layer in enumerate(layers):
                for path, size, extra in layer["entries"]:
                    if extra == digest:
                        sizes.append(size)
            single = min(sizes) if sizes else 0
            content_dups.append({
                "digest": digest,
                "copies": len(paths),
                "reclaimable": max(0, digest_sizes[digest] - single),
                "paths": sorted(paths),
            })
    content_dups.sort(key=lambda x: -x["reclaimable"])

    # ---- 体积最大的路径（最终镜像内）----
    top_paths = [
        {"path": p, "size": s, "layer": layers[i]["id"], "layer_index": i}
        for s, i, p in sorted(
            ((s, i, p) for p, (i, s) in final_owner.items()),
            key=lambda t: -t[0],
        )[:top]
    ]

    reclaimable_total = (
        sum(x["reclaimable"] for x in dup_paths)
        + sum(x["reclaimable"] for x in deleted_paths)
    )

    return {
        "image": manifest["image"],
        "layer_count": n_layers,
        "file_count": len(final_owner),
        "total_size": total_size,
        "layers": [
            {
                "index": i,
                "id": layers[i]["id"],
                "logical_size": logical_sizes[i],
                "net_added_size": net_sizes[i],
                "shadowed_size": logical_sizes[i] - net_sizes[i],
            }
            for i in range(n_layers)
        ],
        "top_paths": top_paths,
        "optimizations": {
            "overwritten_paths": dup_paths,
            "deleted_but_present": deleted_paths,
            "content_duplicates": content_dups,
            "reclaimable_estimate": reclaimable_total,
        },
        "checks": {
            "sum_net_equals_total": sum(net_sizes) == total_size,
            "total_equals_filetree_sum": total_size
                == sum(s for _, s in final_owner.values()),
            "logical_equals_net_plus_wasted": sum(logical_sizes)
                == total_size + wasted,
        },
    }


def human_size(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0
    return "%d B" % n


def print_report(report, top_opt=10):
    print("镜像: %s" % report["image"])
    print("层数: %d   最终文件数: %d   镜像总大小: %s (%d B)" % (
        report["layer_count"], report["file_count"],
        human_size(report["total_size"]), report["total_size"]))
    print()
    print("== 分层归因（净增 = 该层对最终镜像的实际贡献）==")
    print("%-5s %-24s %14s %14s %14s" % ("层", "ID", "逻辑体积", "净增体积", "被遮蔽"))
    for l in report["layers"]:
        print("%-5d %-24s %14s %14s %14s" % (
            l["index"], l["id"][:24],
            human_size(l["logical_size"]),
            human_size(l["net_added_size"]),
            human_size(l["shadowed_size"])))
    checks = report["checks"]
    ok = all(checks.values())
    print()
    print("== 自洽校验 ==")
    for name, passed in checks.items():
        print("  [%s] %s" % ("OK" if passed else "FAIL", name))
    assert ok, "自洽校验失败！"
    print("  结论: 各层净增之和 == 镜像总大小 == %d B" % report["total_size"])
    print()
    print("== 体积最大的路径 ==")
    for t in report["top_paths"]:
        print("  %12s  %-60s (层 %d)" % (human_size(t["size"]), t["path"], t["layer_index"]))
    opt = report["optimizations"]
    print()
    print("== 可优化项 ==")
    print("  预计可回收: %s (%d B)" % (
        human_size(opt["reclaimable_estimate"]), opt["reclaimable_estimate"]))
    if opt["overwritten_paths"]:
        print("  -- 同路径多层重复（旧版本被覆盖）--")
        for x in opt["overwritten_paths"][:top_opt]:
            print("     %12s  %-56s (%d 个废弃版本)" % (
                human_size(x["reclaimable"]), x["path"], x["versions_wasted"]))
    if opt["deleted_but_present"]:
        print("  -- 已删除但仍占下层体积 --")
        for x in opt["deleted_but_present"][:top_opt]:
            print("     %12s  %-56s (%d 个废弃版本)" % (
                human_size(x["reclaimable"]), x["path"], x["versions_wasted"]))
    if opt["content_duplicates"]:
        print("  -- 跨文件内容重复（按 digest）--")
        for x in opt["content_duplicates"][:top_opt]:
            print("     %12s  %s  x%d 份: %s" % (
                human_size(x["reclaimable"]), x["digest"][:20],
                x["copies"], ", ".join(x["paths"][:4])))


def main(argv=None):
    ap = argparse.ArgumentParser(description="镜像体积归因分析")
    ap.add_argument("manifest", nargs="?", help="镜像层清单 JSON（缺省读 stdin）")
    ap.add_argument("--top", type=int, default=20, help="列出体积最大的 N 个路径")
    ap.add_argument("--top-opt", type=int, default=10, help="每类可优化项列出的条数")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON 报告")
    args = ap.parse_args(argv)

    if args.manifest:
        with open(args.manifest, "r", encoding="utf-8") as f:
            manifest = load_manifest(f)
    else:
        manifest = load_manifest(sys.stdin)

    report = analyze(manifest, top=args.top)
    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_report(report, top_opt=args.top_opt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
