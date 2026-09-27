"""演示脚本：同一批绕过用例分别打在修复前/修复后实现上，输出对照证据。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import vulnerable_guard
import secure_guard
from bypass_cases import build_fixture, make_cases, SECRET_CONTENT


def try_vulnerable(root, path):
    try:
        with vulnerable_guard.vulnerable_open(root, path) as f:
            data = f.read()
        return "泄露!" if SECRET_CONTENT in data else "读到(%dB)" % len(data)
    except Exception as exc:
        return "拒绝(%s)" % type(exc).__name__


def try_secure(guard, path):
    try:
        with guard.open_file(path) as f:
            data = f.read()
        return "泄露!" if SECRET_CONTENT in data else "读到(%dB)" % len(data)
    except Exception as exc:
        return "拒绝(%s)" % type(exc).__name__


def main():
    base, root = build_fixture()
    guard = secure_guard.SecurePathGuard(root)
    print("沙箱根: %s\n" % root)
    print("%-38s | %-26s | %s" % ("绕过用例", "修复前 vulnerable", "修复后 secure"))
    print("-" * 96)
    leaked_before = leaked_after = 0
    for name, path in make_cases(base, root):
        before = try_vulnerable(root, path)
        after = try_secure(guard, path)
        leaked_before += "泄露" in before
        leaked_after += "泄露" in after
        print("%-38s | %-26s | %s" % (name, before, after))
    print("-" * 96)
    print("泄露计数: 修复前 %d / 修复后 %d" % (leaked_before, leaked_after))
    return 1 if leaked_after else 0


if __name__ == "__main__":
    sys.exit(main())
