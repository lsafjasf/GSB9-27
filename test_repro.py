"""复现用例：同一组断言分别打在旧逻辑与新引擎上，证明回归测试能抓住原缺陷。

三类问题各一条用例，断言写的是「正确行为应该是什么」，与具体引擎无关；
通过两个 TestCase 分别绑定 legacy_upgrade（旧）与 dataformat（新）执行：

    python3 -m unittest test_repro.TestReproOnLegacy -v   # 预期 3 条全部失败
    python3 -m unittest test_repro.TestReproOnFixed -v    # 预期全绿
"""

import copy
import unittest

from src import legacy_upgrade
from src.dataformat import MissingFieldError, migrate_document

FILL_TZ = {"settings.timezone": "Asia/Shanghai"}


def make_v1_doc():
    return {
        "version": 1,
        "name": "alice",
        "nick": "al",
        "custom_field": "user-defined",       # 顶层未知字段（用户自定义）
        "settings": {
            "theme": "dark",
            "font_size": 14,
            "custom_nested": {"x": 1},         # 嵌套内的未知字段
        },
    }


class ReproCases:
    """与引擎无关的复现断言；子类只需提供 upgrade(doc, fill) -> doc。"""

    def upgrade(self, doc, fill=None):
        raise NotImplementedError

    # 用例 1：跨两个版本升级（v1 -> v3），未知字段必须原样保留。
    def test_cross_two_version_upgrade_preserves_unknown_fields(self):
        out = self.upgrade(make_v1_doc(), fill=FILL_TZ)
        self.assertEqual(out["version"], 3)
        self.assertEqual(out.get("custom_field"), "user-defined",
                         "顶层未知字段在 v1->v2->v3 链上被丢弃")
        self.assertEqual(out["settings"].get("custom_nested"), {"x": 1},
                         "嵌套未知字段在整体重建 settings 时被丢弃")

    # 用例 2：改名字段（nick -> nickname）缺失时必须报错，
    # 不得用默认值 "" 掩盖，使下游无法区分「缺失」与「值就是空串」。
    def test_missing_renamed_field_raises_instead_of_silent_default(self):
        doc = make_v1_doc()
        del doc["nick"]
        with self.assertRaises(MissingFieldError,
                               msg="nick 缺失被静默默认值掩盖，未抛 MissingFieldError"):
            self.upgrade(doc, fill=FILL_TZ)

    # 用例 3：嵌套新增字段（settings.language / settings.timezone）
    # 不得被静默默认值掩盖：有默认值的必须显式标记，无默认值的必须报错。
    def test_nested_added_fields_not_silently_defaulted(self):
        with self.subTest("有默认值的新增字段必须随文档标记"):
            out = self.upgrade(make_v1_doc(), fill=FILL_TZ)
            marked = out.get("__meta__", {}).get("defaults_applied", [])
            self.assertIn("settings.language", marked,
                          "settings.language 被填入默认值 'en' 但无任何标记")
        with self.subTest("无默认值(REQUIRED)的新增字段缺失时必须报错"):
            with self.assertRaises(MissingFieldError,
                                   msg="settings.timezone 缺失被静默默认值掩盖"):
                self.upgrade(make_v1_doc())


class TestReproOnLegacy(ReproCases, unittest.TestCase):
    """同一组用例打在旧逻辑上：预期全部失败（证明用例能抓住原缺陷）。"""

    def upgrade(self, doc, fill=None):
        # 旧逻辑没有 fill 概念（这本身就是缺陷之一），忽略该参数。
        return legacy_upgrade.upgrade(copy.deepcopy(doc))


class TestReproOnFixed(ReproCases, unittest.TestCase):
    """同一组用例打在新引擎上：预期全绿。"""

    def upgrade(self, doc, fill=None):
        migrated, _report = migrate_document(doc, fill=fill)
        return migrated


if __name__ == "__main__":
    unittest.main()
