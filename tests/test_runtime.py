"""运行时调用对照与一致性核对的单元测试。"""
import os
import tempfile
import textwrap
import unittest

from ifacecheck.compare import compare_interfaces
from ifacecheck.model import Interface
from ifacecheck.runtime import (
    FALSE_ALARM, MISS, SAME, SUSPECT, DIFFERENT, V2_ERROR,
    CallCase, check_consistency, load_impl, run_cases,
)

IMPL_V1 = textwrap.dedent("""
    def keep(x):
        return x + 1

    def changed_default(x, mark="!"):
        return f"{x}{mark}"

    def removed_param(path, fmt="png"):
        return {"path": path, "fmt": fmt}

    def silent_bug(a, b):
        return a + b

    def gone():
        return 1
""")

IMPL_V2 = textwrap.dedent("""
    def keep(x):
        return x + 1

    def changed_default(x, mark="."):
        return f"{x}{mark}"

    def removed_param(path):
        return {"path": path, "fmt": "png"}

    def silent_bug(a, b):
        return a - b
""")


def write_impl(tmpdir, name, source):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(source)
    return path


class TestRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_impl = load_impl(
            write_impl(self.tmp.name, "v1.py", IMPL_V1), "t_v1")
        self.new_impl = load_impl(
            write_impl(self.tmp.name, "v2.py", IMPL_V2), "t_v2")

    def tearDown(self):
        self.tmp.cleanup()

    def run_one(self, cases):
        return run_cases(cases, self.old_impl, self.new_impl)

    def test_same_result(self):
        (res,) = self.run_one([CallCase("c1", "keep", args=[1])])
        self.assertEqual(res.outcome, SAME)

    def test_default_change_detected_at_runtime(self):
        (res,) = self.run_one([CallCase("c2", "changed_default", args=["hi"])])
        self.assertEqual(res.outcome, DIFFERENT)

    def test_removed_param_keyword_call_fails(self):
        (res,) = self.run_one([CallCase("c3", "removed_param",
                                    args=["a"], kwargs={"fmt": "jpg"})])
        self.assertEqual(res.outcome, V2_ERROR)

    def test_removed_param_without_kwarg_still_works(self):
        (res,) = self.run_one([CallCase("c4", "removed_param", args=["a"])])
        self.assertEqual(res.outcome, SAME)

    def test_removed_function_fails(self):
        (res,) = self.run_one([CallCase("c5", "gone")])
        self.assertEqual(res.outcome, V2_ERROR)

    def test_added_keys_tracked(self):
        old_impl = self.old_impl
        new_src = IMPL_V1 + "\ndef extended():\n    return {'a': 1, 'b': 2}\n"
        old_src = IMPL_V1 + "\ndef extended():\n    return {'a': 1}\n"
        old2 = load_impl(write_impl(self.tmp.name, "e1.py", old_src), "e1")
        new2 = load_impl(write_impl(self.tmp.name, "e2.py", new_src), "e2")
        (res,) = run_cases([CallCase("c6", "extended")], old2, new2)
        self.assertEqual(res.outcome, DIFFERENT)
        self.assertEqual(res.added_keys, ["b"])
        self.assertEqual(res.removed_keys, [])


class TestConsistency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_impl = load_impl(
            write_impl(self.tmp.name, "v1.py", IMPL_V1), "c_v1")
        self.new_impl = load_impl(
            write_impl(self.tmp.name, "v2.py", IMPL_V2), "c_v2")

    def tearDown(self):
        self.tmp.cleanup()

    def _iface(self, functions):
        return Interface.from_dict({"name": "t", "version": "1",
                                    "functions": functions})

    def test_false_alarm_on_removed_optional_param(self):
        """删除可选参数：静态判破坏性，不传该参数的调用仍正常 -> 误报。"""
        old = self._iface([{"name": "removed_param", "params": [
            {"name": "path", "required": True},
            {"name": "fmt", "required": False, "default": "png"}]}])
        new = self._iface([{"name": "removed_param", "params": [
            {"name": "path", "required": True}]}])
        report = compare_interfaces(old, new)
        (res,) = run_cases([CallCase("c1", "removed_param", args=["a"])],
                           self.old_impl, self.new_impl)
        items = check_consistency(report, [res])
        self.assertEqual([i.kind for i in items], [FALSE_ALARM])

    def test_miss_on_silent_behavior_change(self):
        """接口定义完全没变但实现回归：静态判兼容，实际结果不同 -> 可疑。"""
        iface = self._iface([{"name": "silent_bug", "params": [
            {"name": "a", "type": "int"}, {"name": "b", "type": "int"}]}])
        report = compare_interfaces(iface, iface)
        (res,) = run_cases([CallCase("c1", "silent_bug", args=[3, 1])],
                           self.old_impl, self.new_impl)
        items = check_consistency(report, [res])
        self.assertEqual([i.kind for i in items], [SUSPECT])

    def test_consistent_default_change(self):
        """默认值变化：静态低风险 + 运行时结果不同，属于预期内，不算不一致。"""
        old = self._iface([{"name": "changed_default", "params": [
            {"name": "x"}, {"name": "mark", "required": False, "default": "!"}]}])
        new = self._iface([{"name": "changed_default", "params": [
            {"name": "x"}, {"name": "mark", "required": False, "default": "."}]}])
        report = compare_interfaces(old, new)
        (res,) = run_cases([CallCase("c1", "changed_default", args=["hi"])],
                           self.old_impl, self.new_impl)
        self.assertEqual(check_consistency(report, [res]), [])

    def test_miss_when_compatible_but_v2_errors(self):
        """静态判兼容但实际新版报错 -> 漏报。"""
        iface = self._iface([{"name": "gone", "params": []}])
        report = compare_interfaces(iface, iface)  # 定义没变
        (res,) = run_cases([CallCase("c1", "gone")],
                           self.old_impl, self.new_impl)
        items = check_consistency(report, [res])
        self.assertEqual([i.kind for i in items], [MISS])


if __name__ == "__main__":
    unittest.main()
