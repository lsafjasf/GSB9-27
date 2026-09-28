"""把三条复现契约分别放到旧引擎 / 新引擎上执行。

用法：
  python3 run_repro.py legacy   # 旧逻辑：期望全部失败（红）
  python3 run_repro.py fixed    # 修复逻辑：期望全部通过（绿）
  python3 run_repro.py both     # 两者都跑，先红后绿
退出码 0：结果符合该引擎的预期（legacy 全红 / fixed 全绿）；1：不符合。
"""

import sys
import unittest

from repro_tests import FixedAdapter, LegacyAdapter, build_case

ENGINES = {
    "legacy": (LegacyAdapter(), False),   # 旧引擎预期：全红
    "fixed": (FixedAdapter(), True),     # 新引擎预期：全绿
}


def run(engine_name):
    adapter, expect_pass = ENGINES[engine_name]
    banner = "== 复现契约运行于【%s引擎】（预期：%s） ==" % (
        "旧" if engine_name == "legacy" else "修复",
        "全绿" if expect_pass else "全红（证明用例抓得住原缺陷）")
    print(banner)
    case = build_case(adapter, engine_name)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(case)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    actual_pass = result.wasSuccessful()
    ok = actual_pass == expect_pass
    print("结果：%d 条通过 / %d 条失败，与预期%s"
          % (result.testsRun - len(result.failures) - len(result.errors),
             len(result.failures) + len(result.errors),
             "一致 ✓" if ok else "不一致 ✗"))
    print()
    return ok


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "both"
    if target not in ("legacy", "fixed", "both"):
        print(__doc__)
        return 1
    names = ("legacy", "fixed") if target == "both" else (target,)
    results = [run(name) for name in names]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
