#!/bin/sh
# 运行全部测试（仅标准库，无需安装依赖）
cd "$(dirname "$0")"
python3 -m unittest discover -s tests -t . -v
