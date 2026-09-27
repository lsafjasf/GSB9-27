#!/bin/sh
# 回归测试 + 绕过演示
cd "$(dirname "$0")"
echo "== 回归测试 =="
python3 -m unittest discover -s tests -v
echo
echo "== 绕过用例对照演示 =="
python3 tests/demo_bypass.py
