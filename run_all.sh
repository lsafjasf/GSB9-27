#!/usr/bin/env bash
# 一键运行：自测 -> 标注对拍 -> 性能基准
set -euo pipefail
cd "$(dirname "$0")"
echo "===== 1. 单元测试（噪声抑制 / 边界输入） ====="
python3 -m unittest discover -s tests -v
echo
echo "===== 2. 人工标注对拍 ====="
python3 evaluate.py
echo
echo "===== 3. 性能基准（2 万条） ====="
python3 benchmark.py 20000
