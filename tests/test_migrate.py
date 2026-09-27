"""回归测试：覆盖复现用例 + 修复契约 + 升降级兼容性。

运行: python3 -m unittest discover -s tests -v
"""

import unittest

from dataformat import MissingFieldError, downgrade, upgrade


def make_v1():
    return {
        "version": 1,
        "name": "order-service",
        "timeout": 30,  # 秒
        "server": {"host": "db.internal", "port": 5432},
        "owner": "team-pay",  # 未知字段
    }


class ReproCases(unittest.TestCase):
    """对应 repro_bug.py 中三个稳定复现的缺陷。"""

    def test_cross_two_versions_rename_not_lost(self):
        """用例1: v1->v3 跨两个版本，改名字段 timeout 的值必须保留。"""
        got = upgrade(make_v1(), 1, 3)
        self.assertEqual(got["timeout_ms"], 30000)  # 30s -> 30000ms
        self.assertNotIn("timeout", got)

    def test_nested_new_field_marked_as_defaulted(self):
        """用例2: 嵌套新增字段 server.retries 缺失时填默认值并显式标记。"""
        got = upgrade(make_v1(), 1, 2)
        self.assertEqual(got["server"]["retries"], 3)
        self.assertIn("server.retries", got["_meta"]["defaulted"])

    def test_unknown_fields_preserved(self):
        """用例3: 未知字段 owner 升级后原样保留。"""
        got = upgrade(make_v1(), 1, 3)
        self.assertEqual(got["owner"], "team-pay")


class MissingVsDefault(unittest.TestCase):
    """修复核心契约：区分「字段缺失」与「字段值为默认值」。"""

    def test_missing_required_raises(self):
        doc = make_v1()
        del doc["timeout"]
        with self.assertRaises(MissingFieldError) as ctx:
            upgrade(doc, 1, 2)
        self.assertEqual(ctx.exception.path, "timeout")

    def test_missing_nested_required_raises(self):
        doc = make_v1()
        del doc["server"]["host"]
        with self.assertRaises(MissingFieldError):
            upgrade(doc, 1, 3)

    def test_defaulted_fields_explicitly_marked(self):
        got = upgrade(make_v1(), 1, 3)
        self.assertEqual(got["_meta"]["defaulted"], ["server.retries", "tags"])
        self.assertEqual(got["_meta"]["source_version"], 1)

    def test_user_supplied_value_not_marked(self):
        """用户显式提供的值（即使等于默认值）不得标记为 defaulted。"""
        doc = make_v1()
        doc["server"]["retries"] = 3
        got = upgrade(doc, 1, 2)
        self.assertNotIn("server.retries", got["_meta"]["defaulted"])

    def test_default_factory_not_shared(self):
        """可变默认值（tags=list）每次生成新对象，不得共享引用。"""
        a = upgrade(make_v1(), 1, 3)
        b = upgrade(make_v1(), 1, 3)
        a["tags"].append("x")
        self.assertEqual(b["tags"], [])


class RenameAndRemove(unittest.TestCase):
    def test_rename_transforms_value(self):
        got = upgrade(make_v1(), 1, 2)
        self.assertEqual(got["timeout_ms"], 30000)

    def test_rename_prefers_existing_new_key(self):
        """新旧 key 同时存在时，旧 key 覆盖语义明确：旧数据优先迁移。"""
        doc = make_v1()
        doc["timeout_ms"] = 999
        got = upgrade(doc, 1, 2)
        self.assertEqual(got["timeout_ms"], 30000)
        self.assertNotIn("timeout", got)

    def test_removed_field_archived(self):
        """v3 删除 name，但必须归档到 _meta.removed 而不是消失。"""
        got = upgrade(make_v1(), 1, 3)
        self.assertNotIn("name", got)
        self.assertEqual(got["_meta"]["removed"]["name"], "order-service")


class UnknownFieldPreservation(unittest.TestCase):
    def test_unknown_nested_field_preserved(self):
        doc = make_v1()
        doc["server"]["tls"] = {"enabled": True}
        got = upgrade(doc, 1, 3)
        self.assertEqual(got["server"]["tls"], {"enabled": True})

    def test_roundtrip_keeps_unknown_fields(self):
        doc = make_v1()
        doc["x-custom"] = [1, 2, 3]
        up = upgrade(doc, 1, 3)
        down = downgrade(up, 3, 1)
        self.assertEqual(down["x-custom"], [1, 2, 3])
        self.assertEqual(down["owner"], "team-pay")


class DowngradeCompat(unittest.TestCase):
    """兼容性：新版本数据被旧代码读取（经 downgrade）。"""

    def test_downgrade_restores_removed_field(self):
        up = upgrade(make_v1(), 1, 3)
        down = downgrade(up, 3, 1)
        self.assertEqual(down["name"], "order-service")
        self.assertEqual(down["timeout"], 30)

    def test_downgrade_roundtrip_identity(self):
        doc = make_v1()
        down = downgrade(upgrade(doc, 1, 3), 3, 1)
        self.assertEqual(down["name"], doc["name"])
        self.assertEqual(down["timeout"], doc["timeout"])
        self.assertEqual(down["owner"], doc["owner"])
        self.assertEqual(down["server"]["host"], doc["server"]["host"])
        self.assertEqual(down["server"]["port"], doc["server"]["port"])
        # 往返不保证严格相等：v2 新增的 retries 对 v1 是未知字段，按策略保留。
        self.assertEqual(down["server"]["retries"], 3)

    def test_downgrade_without_archive_raises(self):
        """v3 数据缺少归档且 name 在 v2 必填：显式报错，不填假值。"""
        doc_v3 = {
            "version": 3,
            "timeout_ms": 5000,
            "server": {"host": "h", "port": 80, "retries": 3},
            "tags": [],
        }
        with self.assertRaises(MissingFieldError):
            downgrade(doc_v3, 3, 2)

    def test_downgrade_keeps_newer_unknown_fields(self):
        """v3 的 tags 对 v1 是未知字段，降级后保留，再升级不丢。"""
        up = upgrade(make_v1(), 1, 3)
        up["tags"] = ["a"]
        down = downgrade(up, 3, 1)
        self.assertEqual(down["tags"], ["a"])
        re_up = upgrade(down, 1, 3)
        self.assertEqual(re_up["tags"], ["a"])
        self.assertNotIn("tags", re_up["_meta"]["defaulted"])

    def test_downgrade_non_integral_ms(self):
        up = upgrade(make_v1(), 1, 2)
        up["timeout_ms"] = 1500
        down = downgrade(up, 2, 1)
        self.assertEqual(down["timeout"], 1.5)


if __name__ == "__main__":
    unittest.main()
