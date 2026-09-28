"""三类原缺陷的复现契约：同一份用例分别对旧引擎、新引擎执行。

背景：新引擎写完后第一次跑测试就是全绿，复现脚本是事后补的，
因此"字段被默认值掩盖 / 未知字段静默消失"是否真被回归用例覆盖，
一直没有实证。本模块把每类问题的*期望行为*（而不是旧逻辑的
错误行为）写成一份与具体引擎无关的契约：

  用例1 跨两个版本升级：v1 -> v3，未知字段（含嵌套未知字段）
        不得静默消失，且必须在迁移报告中留痕。
  用例2 改名字段：      nick -> nickname，源字段缺失时必须报错，
        绝不能用 "" 之类默认值冒充；存在时值必须随改名链带走。
  用例3 嵌套新增字段：  有默认值者可填默认值，但必须显式标记
        （defaults_applied / __meta__）；无默认值（REQUIRED）者
        缺失或未 fill 时必须报错，绝不静默填值。

适配层把两个引擎统一成同一接口：
  upgrade(doc, fill=None) -> (data, report)
  MissingFieldError      -> 引擎自己的缺失字段异常类型

验证方式（见 run_repro.py）：
  python3 run_repro.py legacy   # 期望：3 条全红（证明用例确实抓得住原缺陷）
  python3 run_repro.py fixed    # 期望：3 条全绿（修复版满足契约）
"""

import unittest

from src import legacy_upgrade
from src.dataformat import MissingFieldError as FixedMissingFieldError
from src.dataformat import migrate_document


def make_v1_doc():
    return {
        "version": 1,
        "name": "alice",
        "nick": "al",
        "custom_field": "user-defined",
        "settings": {
            "theme": "dark",
            "font_size": 14,
            "custom_nested": {"x": 1},
        },
    }


FILL_TZ = {"settings.timezone": "Asia/Shanghai"}


class LegacyAdapter:
    """旧引擎（src.legacy_upgrade）的适配层。

    旧引擎的 upgrade 不返回报告、不接受 fill、缺失不报异常，
    这里按其真实行为补齐接口；所有缺失信息一律返回 None / 不抛异常。
    """

    MissingFieldError = None
    label = "legacy"

    def upgrade(self, doc, fill=None):
        return legacy_upgrade.upgrade(doc), None


class FixedAdapter:
    """修复引擎（src.dataformat）的适配层。"""

    MissingFieldError = FixedMissingFieldError
    label = "fixed"

    def upgrade(self, doc, fill=None):
        return migrate_document(doc, fill=fill)


class ReproContract:
    """与引擎无关的三条复现契约。混入具体引擎适配类即可执行。"""

    def test_1_cross_two_versions_unknown_fields_must_survive(self):
        """跨两个版本升级（v1 -> v3）：未知字段不得静默消失。"""
        migrated, report = self.engine.upgrade(make_v1_doc(), fill=FILL_TZ)
        self.assertEqual(migrated["version"], 3)
        # 判定依据：升级链上任何一级重建字典时，都必须把不属于
        # schema 的未知字段原样带过，并在 report.preserved 留痕。
        self.assertIn("custom_field", migrated)
        self.assertEqual(migrated["custom_field"], "user-defined")
        self.assertIn("custom_nested", migrated["settings"])
        self.assertEqual(migrated["settings"]["custom_nested"], {"x": 1})
        if report is not None:
            self.assertIn("custom_field", report.preserved)
            self.assertIn("settings.custom_nested", report.preserved)

    def test_2_renamed_field_missing_must_raise_not_default(self):
        """改名字段（nick -> nickname）：缺失必须报错而非默认值掩盖。"""
        doc = make_v1_doc()
        del doc["nick"]
        if self.engine.MissingFieldError is not None:
            # 修复版判定依据：改名源字段缺失是硬错误，
            # MissingFieldError 必须指出缺失路径 nickname/nick。
            with self.assertRaises(self.engine.MissingFieldError) as ctx:
                self.engine.upgrade(doc, fill=FILL_TZ)
            self.assertIn("nick", ctx.exception.path)
        else:
            # 旧引擎不抛异常：契约同样要求不得拿 "" 冒充真实值。
            migrated, _ = self.engine.upgrade(doc, fill=FILL_TZ)
            self.assertNotEqual(migrated.get("nickname"), "",
                                "改名字段缺失被静默默认值 '' 掩盖")

    def test_2b_renamed_field_present_carries_value(self):
        """改名字段存在时：值随改名链带走且旧名不残留。"""
        migrated, report = self.engine.upgrade(make_v1_doc(), fill=FILL_TZ)
        self.assertEqual(migrated.get("nickname"), "al")
        self.assertNotIn("nick", migrated)
        if report is not None:
            self.assertIn(("nick", "nickname"), report.renamed)

    def test_3_nested_added_field_default_must_be_marked(self):
        """嵌套新增字段：默认值可以填，但必须可被识别为"非真实数据"。"""
        migrated, report = self.engine.upgrade(make_v1_doc(), fill=FILL_TZ)
        self.assertEqual(migrated["settings"]["language"], "en")
        if report is not None:
            self.assertIn("settings.language", report.defaults_applied)
        # 判定依据：默认值事实必须随文档持久化（__meta__），
        # 否则下游拿到 "en" 无法区分它是真实数据还是系统填的。
        self.assertIn("__meta__", migrated)
        self.assertIn("settings.language",
                      migrated["__meta__"]["defaults_applied"])

    def test_3b_nested_required_field_without_fill_must_raise(self):
        """嵌套新增的 REQUIRED 字段（settings.timezone）：未提供必须报错。"""
        doc = make_v1_doc()
        if self.engine.MissingFieldError is not None:
            with self.assertRaises(self.engine.MissingFieldError) as ctx:
                self.engine.upgrade(doc)  # 故意不传 fill
            self.assertEqual(ctx.exception.path, "settings.timezone")
            # 提供 fill 后应成功且不得被误标为默认值。
            migrated, report = self.engine.upgrade(doc, fill=FILL_TZ)
            self.assertEqual(migrated["settings"]["timezone"], "Asia/Shanghai")
            self.assertIn("settings.timezone", report.filled)
            self.assertNotIn("settings.timezone", report.defaults_applied)
        else:
            migrated, _ = self.engine.upgrade(doc)  # 旧引擎没有 fill 概念
            self.fail("REQUIRED 嵌套字段缺失未报错，被静默填成 %r"
                      % migrated["settings"].get("timezone"))


def build_case(adapter, engine_name):
    """生成绑定具体引擎的 TestCase 类。"""
    attrs = {
        "engine": adapter,
        "__module__": __name__,
    }
    cls = type("ReproOn%s" % engine_name.title(),
               (ReproContract, unittest.TestCase), attrs)
    return cls
