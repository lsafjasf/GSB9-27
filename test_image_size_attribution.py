#!/usr/bin/env python3
"""image_size_attribution 自测（标准库 unittest）。"""

import unittest

from image_size_attribution import analyze, normalize_path


def make_manifest(layers):
    return {"image": "test:latest", "layers": [
        {"id": "L%d" % i, "files": files} for i, files in enumerate(layers)]}


def f(path, size, digest=None):
    e = {"path": path, "size": size}
    if digest:
        e["digest"] = digest
    return e


def wh(path):
    return {"path": path, "type": "whiteout"}


def opq(path):
    return {"path": path, "type": "opaque"}


class TestNormalize(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(normalize_path("a//b/./c/"), "/a/b/c")
        self.assertEqual(normalize_path("/etc//foo"), "/etc/foo")

    def test_reject_dotdot(self):
        with self.assertRaises(ValueError):
            normalize_path("/a/../b")


class TestAttribution(unittest.TestCase):
    def test_single_layer(self):
        r = analyze(make_manifest([[f("/a", 100), f("/b", 200)]]))
        self.assertEqual(r["total_size"], 300)
        self.assertEqual(r["layers"][0]["net_added_size"], 300)
        self.assertTrue(all(r["checks"].values()))

    def test_empty_layers(self):
        r = analyze(make_manifest([[], [], []]))
        self.assertEqual(r["total_size"], 0)
        self.assertEqual([l["net_added_size"] for l in r["layers"]], [0, 0, 0])
        self.assertTrue(all(r["checks"].values()))

    def test_zero_layers(self):
        r = analyze(make_manifest([]))
        self.assertEqual(r["total_size"], 0)
        self.assertEqual(r["layer_count"], 0)

    def test_overwrite_same_path(self):
        # 同一路径被上层覆盖：旧版本体积不计入下层净增
        r = analyze(make_manifest([
            [f("/app/bin", 1000)],
            [f("/app/bin", 100)],
        ]))
        self.assertEqual(r["total_size"], 100)
        self.assertEqual(r["layers"][0]["net_added_size"], 0)
        self.assertEqual(r["layers"][1]["net_added_size"], 100)
        opt = r["optimizations"]["overwritten_paths"]
        self.assertEqual(len(opt), 1)
        self.assertEqual(opt[0]["path"], "/app/bin")
        self.assertEqual(opt[0]["reclaimable"], 1000)

    def test_repeated_overwrite_many_layers(self):
        # 同一路径反复覆盖 1000 层：只有最后一层计入
        n = 1000
        layers = [[f("/x", i + 1)] for i in range(n)]
        r = analyze(make_manifest(layers))
        self.assertEqual(r["total_size"], n)
        self.assertEqual(r["layers"][-1]["net_added_size"], n)
        self.assertEqual(sum(l["net_added_size"] for l in r["layers"]), n)
        opt = r["optimizations"]["overwritten_paths"]
        self.assertEqual(opt[0]["reclaimable"], sum(range(1, n)))
        self.assertEqual(opt[0]["versions_wasted"], n - 1)

    def test_whiteout_delete(self):
        # 删除的文件仍占下层体积，计入可回收
        r = analyze(make_manifest([
            [f("/keep", 10), f("/secret.key", 5000)],
            [wh("/secret.key")],
        ]))
        self.assertEqual(r["total_size"], 10)
        self.assertEqual(r["layers"][0]["net_added_size"], 10)
        d = r["optimizations"]["deleted_but_present"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["path"], "/secret.key")
        self.assertEqual(d[0]["reclaimable"], 5000)
        self.assertEqual(r["optimizations"]["reclaimable_estimate"], 5000)

    def test_opaque_dir(self):
        r = analyze(make_manifest([
            [f("/var/cache/a", 100), f("/var/cache/b", 200), f("/etc/c", 5)],
            [opq("/var/cache")],
        ]))
        self.assertEqual(r["total_size"], 5)
        d = {x["path"]: x["reclaimable"]
             for x in r["optimizations"]["deleted_but_present"]}
        self.assertEqual(d, {"/var/cache/a": 100, "/var/cache/b": 200})

    def test_delete_then_recreate(self):
        # 删除后重建：重建版本计入，旧版本计入可回收
        r = analyze(make_manifest([
            [f("/cfg", 1000)],
            [wh("/cfg")],
            [f("/cfg", 50)],
        ]))
        self.assertEqual(r["total_size"], 50)
        self.assertEqual(r["layers"][2]["net_added_size"], 50)
        d = r["optimizations"]["deleted_but_present"]
        self.assertEqual(d[0]["reclaimable"], 1000)

    def test_overwrite_then_delete(self):
        # 先覆盖再删除：两个旧版本都可回收
        r = analyze(make_manifest([
            [f("/f", 100)],
            [f("/f", 200)],
            [wh("/f")],
        ]))
        self.assertEqual(r["total_size"], 0)
        opt = r["optimizations"]
        self.assertEqual(opt["overwritten_paths"][0]["reclaimable"], 100)
        self.assertEqual(opt["deleted_but_present"][0]["reclaimable"], 200)
        self.assertEqual(opt["reclaimable_estimate"], 300)

    def test_many_small_files(self):
        # 大量小文件：10 层 x 1 万文件
        layers = [[f("/dir%d/file%04d" % (i, j), 10) for j in range(10000)]
                  for i in range(10)]
        r = analyze(make_manifest(layers))
        self.assertEqual(r["total_size"], 10 * 10000 * 10)
        self.assertEqual(r["file_count"], 100000)
        self.assertTrue(all(r["checks"].values()))

    def test_many_layers_sparse(self):
        # 极多层（2000 层），每层少量文件
        n = 2000
        layers = [[f("/f%d" % i, 1)] for i in range(n)]
        r = analyze(make_manifest(layers))
        self.assertEqual(r["total_size"], n)
        self.assertEqual(r["layer_count"], n)
        self.assertEqual(sum(l["net_added_size"] for l in r["layers"]), n)

    def test_top_paths(self):
        r = analyze(make_manifest([
            [f("/big", 10**6), f("/mid", 10**4), f("/small", 1)],
        ]), top=2)
        self.assertEqual(len(r["top_paths"]), 2)
        self.assertEqual(r["top_paths"][0]["path"], "/big")
        self.assertEqual(r["top_paths"][1]["path"], "/mid")

    def test_content_duplicates(self):
        r = analyze(make_manifest([
            [f("/a/lib.so", 800, digest="sha256:x"),
             f("/b/lib.so", 800, digest="sha256:x"),
             f("/c/other", 10, digest="sha256:y")],
        ]))
        cd = r["optimizations"]["content_duplicates"]
        self.assertEqual(len(cd), 1)
        self.assertEqual(cd[0]["copies"], 2)
        self.assertEqual(cd[0]["reclaimable"], 800)

    def test_invariant_sum_equals_total(self):
        # 混合场景下的强不变式
        layers = [
            [f("/a", 100), f("/b", 200), f("/d/e", 300)],
            [f("/a", 150), wh("/b")],
            [],
            [f("/a", 50), f("/c", 70), opq("/d")],
        ]
        r = analyze(make_manifest(layers))
        nets = [l["net_added_size"] for l in r["layers"]]
        self.assertEqual(sum(nets), r["total_size"])
        self.assertEqual(r["total_size"], 50 + 70)
        self.assertEqual(nets, [0, 0, 0, 120])
        self.assertEqual(r["optimizations"]["reclaimable_estimate"],
                         100 + 150 + 200 + 300)


if __name__ == "__main__":
    unittest.main(verbosity=2)
