"""回归测试与兼容性测试。运行：python3 -m unittest test_upgrade -v"""

import copy
import unittest

from src import legacy_upgrade
from src.dataformat import (
    CURRENT_VERSION,
    MigrationError,
    MissingFieldError,
    UnsupportedVersionError,
    compatibility_report,
    merge_reader_write,
    migrate,
    migrate_document,
    reader_view,
)


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


# 三类原缺陷的"行为契约"（repro_tests.py）在修复引擎上的固化：
# 同一份用例在旧引擎上为红（python3 run_repro.py legacy），
# 在修复引擎上必须持续为绿。此处把修复引擎版本纳入常规回归套件。
from repro_tests import FixedAdapter, build_case

ReproContractOnFixed = build_case(FixedAdapter(), "fixed")


def make_v2_doc():
    return {
        "version": 2,
        "name": "alice",
        "nickname": "al",
        "email": "a@example.com",
        "custom_field": "user-defined",
        "settings": {
            "theme": "dark",
            "font_size": 14,
            "language": "zh",
            "custom_nested": {"x": 1},
        },
    }


FILL_TZ = {"settings.timezone": "Asia/Shanghai"}


class TestLegacyBugRepro(unittest.TestCase):
    """固化旧逻辑的缺陷行为（修复前的复现用例）。"""

    def test_legacy_loses_unknown_fields_across_two_versions(self):
        upgraded = legacy_upgrade.upgrade(make_v1_doc())
        self.assertNotIn("custom_field", upgraded)
        self.assertNotIn("custom_nested", upgraded["settings"])

    def test_legacy_renamed_field_missing_silently_defaulted(self):
        doc = make_v1_doc()
        del doc["nick"]
        upgraded = legacy_upgrade.upgrade(doc)
        self.assertEqual(upgraded["nickname"], "")  # 缺失被默认值掩盖

    def test_legacy_nested_added_field_silently_defaulted(self):
        upgraded = legacy_upgrade.upgrade(make_v1_doc())
        self.assertEqual(upgraded["settings"]["language"], "en")
        self.assertEqual(upgraded["settings"]["timezone"], "UTC")
        self.assertNotIn("__meta__", upgraded)  # 无任何"这是默认值"的标记


class TestCrossVersionUpgrade(unittest.TestCase):
    """用例 1：跨两个版本升级（v1 -> v3）。"""

    def test_unknown_fields_preserved_across_two_versions(self):
        migrated, report = migrate_document(make_v1_doc(), fill=FILL_TZ)
        self.assertEqual(migrated["version"], 3)
        self.assertEqual(migrated["custom_field"], "user-defined")
        self.assertEqual(migrated["settings"]["custom_nested"], {"x": 1})
        self.assertIn("custom_field", report.preserved)
        self.assertIn("settings.custom_nested", report.preserved)

    def test_known_values_carried_through_chain(self):
        migrated, _ = migrate_document(make_v1_doc(), fill=FILL_TZ)
        self.assertEqual(migrated["name"], "alice")
        self.assertEqual(migrated["nickname"], "al")          # nick 改名链
        self.assertEqual(migrated["contact_email"], None)     # email 新增后又改名
        self.assertEqual(migrated["settings"]["theme"], "dark")
        self.assertEqual(migrated["settings"]["language"], "en")
        self.assertEqual(migrated["settings"]["timezone"], "Asia/Shanghai")
        self.assertNotIn("font_size", migrated["settings"])   # v3 已删除
        self.assertNotIn("nick", migrated)
        self.assertNotIn("email", migrated)

    def test_intermediate_step_v1_to_v2(self):
        migrated, report = migrate_document(make_v1_doc(), to_version=2)
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(migrated["nickname"], "al")
        self.assertIsNone(migrated["email"])
        self.assertEqual(migrated["settings"]["font_size"], 14)
        self.assertEqual(migrated["custom_field"], "user-defined")
        self.assertIn(("nick", "nickname"), report.renamed)


class TestRenamedField(unittest.TestCase):
    """用例 2：改名字段。"""

    def test_rename_preserves_value(self):
        migrated, report = migrate_document(make_v2_doc(), fill=FILL_TZ)
        self.assertEqual(migrated["contact_email"], "a@example.com")
        self.assertNotIn("email", migrated)
        self.assertIn(("email", "contact_email"), report.renamed)

    def test_missing_renamed_field_raises_not_defaults(self):
        doc = make_v1_doc()
        del doc["nick"]
        with self.assertRaises(MissingFieldError):
            migrate_document(doc, fill=FILL_TZ)

    def test_missing_required_kept_field_raises(self):
        doc = make_v1_doc()
        del doc["name"]
        with self.assertRaises(MissingFieldError):
            migrate_document(doc, fill=FILL_TZ)


class TestNestedAddedField(unittest.TestCase):
    """用例 3：嵌套结构内的新增字段。"""

    def test_nested_add_with_default_is_marked(self):
        migrated, report = migrate_document(make_v1_doc(), fill=FILL_TZ)
        self.assertEqual(migrated["settings"]["language"], "en")
        self.assertIn("settings.language", report.defaults_applied)
        # 默认值事实随文档持久化，下游可显式识别
        self.assertIn("settings.language",
                      migrated["__meta__"]["defaults_applied"])

    def test_nested_add_required_without_fill_raises(self):
        with self.assertRaises(MissingFieldError) as ctx:
            migrate_document(make_v2_doc())
        self.assertEqual(ctx.exception.path, "settings.timezone")

    def test_nested_add_required_with_fill_succeeds(self):
        migrated, report = migrate_document(make_v2_doc(), fill=FILL_TZ)
        self.assertEqual(migrated["settings"]["timezone"], "Asia/Shanghai")
        self.assertIn("settings.timezone", report.filled)
        self.assertNotIn("settings.timezone", report.defaults_applied)


class TestMissingVsDefault(unittest.TestCase):
    """区分"字段缺失"与"字段值恰好等于默认值"。"""

    def test_real_value_equal_to_default_is_not_marked(self):
        doc = make_v2_doc()
        doc["tags"] = []  # 真实数据里已有 tags（对 v2 是未知字段），值恰为默认值
        migrated, report = migrate_document(doc, fill=FILL_TZ)
        self.assertEqual(migrated["tags"], [])
        self.assertNotIn("tags", report.defaults_applied)
        self.assertNotIn("tags",
                         migrated.get("__meta__", {}).get("defaults_applied", []))

    def test_absent_field_gets_default_and_is_marked(self):
        migrated, report = migrate_document(make_v2_doc(), fill=FILL_TZ)
        self.assertEqual(migrated["tags"], [])
        self.assertIn("tags", report.defaults_applied)

    def test_default_mutable_copy_is_isolated(self):
        m1, _ = migrate_document(make_v2_doc(), fill=FILL_TZ)
        m2, _ = migrate_document(make_v2_doc(), fill=FILL_TZ)
        m1["tags"].append("x")
        self.assertEqual(m2["tags"], [])


class TestUnknownFieldPreservation(unittest.TestCase):
    def test_unknown_fields_survive_write_back(self, ):
        migrated, _ = migrate_document(make_v1_doc(), fill=FILL_TZ)
        # 模拟下游读写一轮（序列化 -> 反序列化 -> 再迁移已无操作）
        round_tripped = copy.deepcopy(migrated)
        again, report = migrate_document(round_tripped, to_version=CURRENT_VERSION)
        self.assertEqual(again["custom_field"], "user-defined")
        self.assertEqual(again["settings"]["custom_nested"], {"x": 1})

    def test_deleted_field_is_removed_and_reported(self):
        migrated, report = migrate_document(make_v2_doc(), fill=FILL_TZ)
        self.assertNotIn("font_size", migrated["settings"])
        self.assertIn("settings.font_size", report.deleted)


class TestDowngradeCompat(unittest.TestCase):
    """降级：新版本数据被旧版本代码读取。"""

    def test_compatibility_report_v2_reader_v3_data(self):
        report = compatibility_report(reader_version=2, data_version=3)
        # v2 代码期望的 email / settings.font_size 在 v3 已不存在
        self.assertIn("email", report["missing_for_reader"])
        self.assertIn("settings.font_size", report["missing_for_reader"])
        # v3 新增的 contact_email / tags / settings.timezone 对 v2 不可见
        self.assertIn("contact_email", report["invisible_to_reader"])
        self.assertIn("tags", report["invisible_to_reader"])
        self.assertIn("settings.timezone", report["invisible_to_reader"])

    def test_v2_reader_sees_only_known_fields(self):
        migrated, _ = migrate_document(make_v1_doc(), fill=FILL_TZ)
        view = reader_view(migrated, reader_version=2)
        self.assertIn("name", view)
        self.assertIn("settings", view)
        self.assertNotIn("contact_email", view)  # v2 不认识改名后的字段
        self.assertNotIn("tags", view)

    def test_v2_reader_write_back_preserves_v3_only_fields(self):
        migrated, _ = migrate_document(make_v1_doc(), fill=FILL_TZ)
        updated = merge_reader_write(migrated, reader_version=2,
                                     updates={"name": "bob"})
        self.assertEqual(updated["name"], "bob")
        self.assertEqual(updated["contact_email"], None)
        self.assertEqual(updated["settings"]["timezone"], "Asia/Shanghai")
        self.assertEqual(updated["custom_field"], "user-defined")

    def test_v2_reader_cannot_inject_unknown_fields(self):
        migrated, _ = migrate_document(make_v1_doc(), fill=FILL_TZ)
        with self.assertRaises(MigrationError):
            merge_reader_write(migrated, reader_version=2,
                               updates={"evil_field": 1})

    def test_downgrade_migrate_is_rejected(self):
        migrated, _ = migrate_document(make_v1_doc(), fill=FILL_TZ)
        with self.assertRaises(UnsupportedVersionError):
            migrate(migrated, 3, 1)


if __name__ == "__main__":
    unittest.main()
