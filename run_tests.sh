#!/bin/sh
# 运行全部检查与测试（仅标准库，无需安装依赖）
cd "$(dirname "$0")" || exit 2

# 1) 时钟使用检查：实现中禁止直接取系统时间/直接休眠，有违规则失败退出
python3 tools/check_clock_usage.py src || exit $?

# 2) 单元测试
python3 -m unittest discover -s tests -t . -v
