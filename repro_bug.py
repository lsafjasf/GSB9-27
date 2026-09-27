"""复现脚本：用旧版升级逻辑稳定复现三类字段丢失/掩盖问题。

运行：python3 repro_bug.py
退出码：复现成功（即 bug 确实存在）为 0；若旧逻辑已不再丢数据则为 1。
"""

import json
import sys

from src import legacy_upgrade


def make_v1_doc():
    return {
        "version": 1,
        "name": "alice",
        "nick": "al",
        "custom_field": "user-defined",        # 未知字段（用户自定义）
        "settings": {
            "theme": "dark",
            "font_size": 14,
            "custom_nested": {"x": 1},          # 嵌套内的未知字段
        },
    }


def main():
    failures = []

    # 用例 1：跨两个版本升级（v1 -> v3），未知字段被丢弃
    upgraded = legacy_upgrade.upgrade(make_v1_doc())
    if upgraded.get("custom_field") != "user-defined":
        failures.append("用例1 跨版本升级：顶层未知字段 custom_field 丢失")
    if upgraded["settings"].get("custom_nested") != {"x": 1}:
        failures.append("用例1 跨版本升级：嵌套未知字段 settings.custom_nested 丢失")

    # 用例 2：改名字段缺失时被静默默认值掩盖
    doc = make_v1_doc()
    del doc["nick"]                              # 数据里本来就没有 nick
    upgraded = legacy_upgrade.upgrade(doc)
    if upgraded.get("nickname") == "":
        failures.append(
            "用例2 改名字段：nick 缺失被静默填成 ''，下游无法区分缺失与空串")

    # 用例 3：嵌套结构内的新增字段被静默默认值掩盖
    upgraded = legacy_upgrade.upgrade(make_v1_doc())
    if upgraded["settings"].get("language") == "en" \
            and upgraded["settings"].get("timezone") == "UTC":
        failures.append(
            "用例3 嵌套新增字段：settings.language/timezone 被静默填默认值，"
            "且无任何标记")

    # 附加：缺失必填字段 name 也被默认值掩盖
    doc = make_v1_doc()
    del doc["name"]
    upgraded = legacy_upgrade.upgrade(doc)
    if upgraded.get("name") == "":
        failures.append("附加 必填字段 name 缺失被静默填成 ''")

    print("旧版升级逻辑输出：")
    print(json.dumps(legacy_upgrade.upgrade(make_v1_doc()),
                     indent=2, ensure_ascii=False, sort_keys=True))
    print()
    if failures:
        print("复现成功，旧逻辑存在以下问题：")
        for f in failures:
            print("  - " + f)
        return 0
    print("未复现：旧逻辑似乎已不再丢数据？")
    return 1


if __name__ == "__main__":
    sys.exit(main())
