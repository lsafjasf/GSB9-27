#!/usr/bin/env bash
# 一键运行全部测试，仅依赖 Python 3 标准库。
set -euo pipefail
cd "$(dirname "$0")"
python3 -m unittest discover -s tests -v
