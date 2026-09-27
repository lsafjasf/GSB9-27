#!/usr/bin/env python3
"""复现脚本：旧版（有缺陷的）升级逻辑如何静默丢字段。

这里的 legacy_upgrade 是修复前的实现，存在三个典型缺陷：
  1. 用 dict.get(key, default) 取新数字段，字段缺失时被默认值悄悄掩盖；
  2. 改名只写新 key，不读旧 key，旧数据里的值直接丢失；
  3. 重建结果字典时只拷贝已知字段，未知字段被丢弃。

运行: python3 repro_bug.py
"""

import json


def legacy_upgrade_v1_to_v2(data):
    """缺陷版 v1 -> v2。"""
    server = data.get("server", {})
    return {
        "version": 2,
        "name": data.get("name", ""),
        # 缺陷2: timeout 已改名为 timeout_ms，但这里没有读旧 key "timeout"，
        # 旧数据里的 timeout=30 被丢掉，下游读到默认值 5000 并当成真实值。
        "timeout_ms": data.get("timeout_ms", 5000),
        "server": {
            "host": server.get("host", "localhost"),
            "port": server.get("port", 80),
            # 缺陷1: 新增嵌套字段 server.retries，缺失时静默填默认值，
            # 下游无法区分"用户没配"和"用户配了 3"。
            "retries": server.get("retries", 3),
        },
        # 缺陷3: 只拷贝已知字段，data 里的未知字段（如 "owner"）被丢弃。
    }


def legacy_upgrade_v2_to_v3(data):
    """缺陷版 v2 -> v3。"""
    return {
        "version": 3,
        "timeout_ms": data.get("timeout_ms", 5000),
        "server": data.get("server", {}),
        "tags": data.get("tags", []),
        # name 在 v3 被删除，直接丢弃且不留痕迹，降级时无法恢复。
    }


def legacy_upgrade(data, from_version, to_version):
    upgrades = {(1, 2): legacy_upgrade_v1_to_v2, (2, 3): legacy_upgrade_v2_to_v3}
    out = data
    for v in range(from_version, to_version):
        out = upgrades[(v, v + 1)](out)
    return out


def main():
    print("=== 用例1: 跨两个版本升级 (v1 -> v3)，改名字段丢失 ===")
    doc_v1 = {
        "version": 1,
        "name": "order-service",
        "timeout": 30,            # v1 语义: 秒
        "server": {"host": "db.internal", "port": 5432},
        "owner": "team-pay",      # 未知字段，旧代码不认识但业务需要
    }
    got = legacy_upgrade(doc_v1, 1, 3)
    print(json.dumps(got, indent=2, ensure_ascii=False))
    assert got["timeout_ms"] == 5000, "预期缺陷: 30s 被默认值 5000ms 掩盖"
    print("!! BUG: timeout=30 丢失，timeout_ms 变成默认值 5000，下游当成真实值")

    print("\n=== 用例2: 嵌套结构内的新增字段无法区分缺失与默认值 ===")
    got2 = legacy_upgrade(doc_v1, 1, 2)
    assert got2["server"]["retries"] == 3
    print("!! BUG: server.retries 用户从未配置，却被填入 3，无任何标记")

    print("\n=== 用例3: 未知字段被丢弃 ===")
    assert "owner" not in got
    print("!! BUG: 未知字段 owner='team-pay' 在升级后消失")

    print("\n=== 用例4: 删除字段不留痕迹，降级不可恢复 ===")
    assert "name" not in got
    print("!! BUG: name='order-service' 被删除且未归档，降级回 v1 时永久丢失")


if __name__ == "__main__":
    main()
