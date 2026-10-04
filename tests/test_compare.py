"""静态判定逻辑的单元测试，覆盖要求列出的各类情形与边界。"""
import unittest

from ifacecheck.compare import (
    BREAKING, CAUTION, COMPATIBLE,
    PARAM_ADDED_OPTIONAL, PARAM_ADDED_REQUIRED, PARAM_DEFAULT_CHANGED,
    PARAM_REMOVED_OPTIONAL, PARAM_REMOVED_REQUIRED, PARAM_TYPE_CHANGED,
    RETURN_EXTENDED, RETURN_FIELD_REMOVED, FUNCTION_ADDED, FUNCTION_REMOVED,
    compare_interfaces,
)
from ifacecheck.model import Interface


def make_iface(functions):
    return Interface.from_dict({"name": "t", "version": "1", "functions": functions})


def fn(name, params=None, returns=None):
    f = {"name": name}
    if params is not None:
        f["params"] = params
    if returns is not None:
        f["returns"] = returns
    return f


class TestCompare(unittest.TestCase):
    def test_fully_compatible(self):
        old = make_iface([fn("ping", [{"name": "m", "type": "str"}])])
        new = make_iface([fn("ping", [{"name": "m", "type": "str"}])])
        report = compare_interfaces(old, new)
        self.assertEqual(report.judgments, [])
        self.assertEqual(report.max_level, COMPATIBLE)

    def test_add_optional_param_is_compatible(self):
        old = make_iface([fn("f")])
        new = make_iface([fn("f", [{"name": "x", "type": "int",
                                    "required": False, "default": 1}])])
        report = compare_interfaces(old, new)
        self.assertEqual([j.kind for j in report.judgments], [PARAM_ADDED_OPTIONAL])
        self.assertEqual(report.max_level, COMPATIBLE)

    def test_add_required_param_is_breaking(self):
        old = make_iface([fn("f")])
        new = make_iface([fn("f", [{"name": "x", "type": "int",
                                    "required": True}])])
        report = compare_interfaces(old, new)
        self.assertEqual([j.kind for j in report.judgments], [PARAM_ADDED_REQUIRED])
        self.assertEqual(report.max_level, BREAKING)

    def test_remove_optional_param_is_breaking(self):
        old = make_iface([fn("f", [{"name": "x", "required": False,
                                    "default": 1}])])
        new = make_iface([fn("f")])
        report = compare_interfaces(old, new)
        self.assertEqual([j.kind for j in report.judgments],
                         [PARAM_REMOVED_OPTIONAL])
        self.assertEqual(report.max_level, BREAKING)

    def test_remove_required_param_is_breaking(self):
        old = make_iface([fn("f", [{"name": "x", "required": True}])])
        new = make_iface([fn("f")])
        report = compare_interfaces(old, new)
        self.assertEqual([j.kind for j in report.judgments],
                         [PARAM_REMOVED_REQUIRED])
        self.assertEqual(report.max_level, BREAKING)

    def test_default_change_is_caution(self):
        old = make_iface([fn("f", [{"name": "x", "required": False,
                                    "default": "!"}])])
        new = make_iface([fn("f", [{"name": "x", "required": False,
                                    "default": "."}])])
        report = compare_interfaces(old, new)
        self.assertEqual([j.kind for j in report.judgments], [PARAM_DEFAULT_CHANGED])
        self.assertEqual(report.max_level, CAUTION)
        self.assertIn("静默行为变化", report.judgments[0].rationale)

    def test_return_extension_is_caution(self):
        ret_old = {"type": "object", "fields": [{"name": "id", "type": "int"}]}
        ret_new = {"type": "object", "fields": [{"name": "id", "type": "int"},
                                                {"name": "email", "type": "str"}]}
        report = compare_interfaces(make_iface([fn("g", returns=ret_old)]),
                                    make_iface([fn("g", returns=ret_new)]))
        self.assertEqual([j.kind for j in report.judgments], [RETURN_EXTENDED])
        self.assertEqual(report.max_level, CAUTION)

    def test_return_field_removed_is_breaking(self):
        ret_old = {"type": "object", "fields": [{"name": "id", "type": "int"},
                                                {"name": "email", "type": "str"}]}
        ret_new = {"type": "object", "fields": [{"name": "id", "type": "int"}]}
        report = compare_interfaces(make_iface([fn("g", returns=ret_old)]),
                                    make_iface([fn("g", returns=ret_new)]))
        self.assertEqual([j.kind for j in report.judgments], [RETURN_FIELD_REMOVED])
        self.assertEqual(report.max_level, BREAKING)

    def test_param_type_changed_is_breaking(self):
        old = make_iface([fn("f", [{"name": "x", "type": "int"}])])
        new = make_iface([fn("f", [{"name": "x", "type": "str"}])])
        report = compare_interfaces(old, new)
        self.assertEqual([j.kind for j in report.judgments], [PARAM_TYPE_CHANGED])
        self.assertEqual(report.max_level, BREAKING)

    def test_function_added_and_removed(self):
        old = make_iface([fn("keep"), fn("gone")])
        new = make_iface([fn("keep"), fn("fresh")])
        report = compare_interfaces(old, new)
        kinds = {j.kind for j in report.judgments}
        self.assertEqual(kinds, {FUNCTION_ADDED, FUNCTION_REMOVED})
        self.assertEqual(report.max_level, BREAKING)

    # ---- 边界用例 ----

    def test_default_zero_vs_missing(self):
        """default 0 必须被识别为“有默认值”，不能与无默认值混淆。"""
        old = make_iface([fn("f", [{"name": "x", "required": False, "default": 0}])])
        new = make_iface([fn("f", [{"name": "x", "required": True}])])
        report = compare_interfaces(old, new)
        kinds = {j.kind for j in report.judgments}
        self.assertIn("PARAM_DEFAULT_REMOVED", kinds)

    def test_default_none_vs_missing(self):
        """default null 同样是有默认值。"""
        old = make_iface([fn("f", [{"name": "x", "required": False,
                                    "default": None}])])
        new = make_iface([fn("f", [{"name": "x", "required": False,
                                    "default": 5}])])
        report = compare_interfaces(old, new)
        self.assertEqual([j.kind for j in report.judgments], [PARAM_DEFAULT_CHANGED])

    def test_empty_interface(self):
        report = compare_interfaces(make_iface([]), make_iface([]))
        self.assertEqual(report.judgments, [])
        self.assertEqual(report.max_level, COMPATIBLE)

    def test_param_order_irrelevant(self):
        """参数顺序变化不算接口变化（按名字匹配）。"""
        old = make_iface([fn("f", [{"name": "a"}, {"name": "b"}])])
        new = make_iface([fn("f", [{"name": "b"}, {"name": "a"}])])
        report = compare_interfaces(old, new)
        self.assertEqual(report.judgments, [])

    def test_required_loosened_is_compatible(self):
        old = make_iface([fn("f", [{"name": "x", "required": True}])])
        new = make_iface([fn("f", [{"name": "x", "required": False,
                                    "default": 3}])])
        report = compare_interfaces(old, new)
        self.assertEqual(report.max_level, COMPATIBLE)


if __name__ == "__main__":
    unittest.main()
