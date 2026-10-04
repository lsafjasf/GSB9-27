"""端到端：跑 demo 目录下的真实数据，验证四类情形都被覆盖。"""
import os
import unittest

from ifacecheck.compare import (
    CAUTION, COMPATIBLE, PARAM_DEFAULT_CHANGED, PARAM_REMOVED_OPTIONAL,
    RETURN_EXTENDED, compare_interfaces,
)
from ifacecheck.model import Interface
from ifacecheck.runtime import (
    FALSE_ALARM, SUSPECT, check_consistency, load_cases, load_impl, run_cases,
)

DEMO = os.path.join(os.path.dirname(__file__), "..", "demo")


class TestDemoEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = Interface.from_json_file(os.path.join(DEMO, "iface_v1.json"))
        cls.new = Interface.from_json_file(os.path.join(DEMO, "iface_v2.json"))
        cls.report = compare_interfaces(cls.old, cls.new)
        cls.cases = load_cases(os.path.join(DEMO, "cases.json"))
        cls.results = run_cases(
            cls.cases,
            load_impl(os.path.join(DEMO, "impl_v1.py"), "demo_v1"),
            load_impl(os.path.join(DEMO, "impl_v2.py"), "demo_v2"),
        )
        cls.inconsistencies = check_consistency(cls.report, cls.results)

    def test_required_scenarios_covered(self):
        kinds = {j.kind for j in self.report.judgments}
        # 删除可选参数 / 默认值变化 / 返回结构扩展
        self.assertIn(PARAM_REMOVED_OPTIONAL, kinds)
        self.assertIn(PARAM_DEFAULT_CHANGED, kinds)
        self.assertIn(RETURN_EXTENDED, kinds)
        # 完全兼容：ping 无任何判定项
        self.assertEqual(self.report.for_function("ping"), [])

    def test_default_change_and_return_extension_are_caution(self):
        for j in self.report.judgments:
            if j.kind in (PARAM_DEFAULT_CHANGED, RETURN_EXTENDED):
                self.assertEqual(j.level, CAUTION)
                self.assertTrue(j.rationale)

    def test_inconsistency_list(self):
        kinds = {i.kind for i in self.inconsistencies}
        self.assertIn(FALSE_ALARM, kinds)  # resize 不传 fmt 仍正常
        self.assertIn(SUSPECT, kinds)      # sum2 接口未变但实现回归

    def test_every_case_has_result(self):
        self.assertEqual(len(self.results), len(self.cases))
        self.assertTrue(all(r.outcome for r in self.results))


if __name__ == "__main__":
    unittest.main()
