#!/usr/bin/env python3
"""内容协商对拍脚本。

随机生成「客户端 Accept 偏好 + 服务端能力列表」组合，比较两个
独立实现的选择结果；发现分歧时用贪心缩小（delta debugging 风格）
输出最小反例。

用法：
    python3 duel.py                         # 主库 vs 参照实现（默认）
    python3 duel.py --impl buggy --seed 1   # 主库 vs 缺陷变体
    python3 duel.py --iterations 20000
"""

from __future__ import annotations

import argparse
import json
import random

import content_negotiation
import reference_impl

_TYPES = ["text", "application", "image"]
_SUBTYPES = ["html", "json", "plain", "png"]
_PARAMS = [("level", "1"), ("level", "2"), ("format", "raw")]
_QS = ["0", "0.1", "0.5", "0.9", "1"]


def random_case(rng):
    """生成一个随机测试用例。

    返回 (header_or_none, offers)。header 为 None 表示客户端
    完全没有提供 Accept 偏好。
    """
    if rng.random() < 0.05:
        header = None
    else:
        entries = []
        for _ in range(rng.randint(0, 5)):
            form = rng.randrange(5)
            if form == 0:
                token = "*/*"
            elif form == 1:
                token = f"{rng.choice(_TYPES)}/*"
            else:
                token = f"{rng.choice(_TYPES)}/{rng.choice(_SUBTYPES)}"
            if form >= 2 and rng.random() < 0.3:
                extra = "".join(
                    f";{k}={v}"
                    for k, v in rng.sample(_PARAMS, rng.randint(1, 2))
                )
                token += extra
            if rng.random() < 0.7:
                token += f";q={rng.choice(_QS)}"
            entries.append(token)
        header = ", ".join(entries)
    offers = []
    for _ in range(rng.randint(0, 4)):
        token = f"{rng.choice(_TYPES)}/{rng.choice(_SUBTYPES)}"
        if rng.random() < 0.25:
            for k, v in rng.sample(_PARAMS, rng.randint(1, 2)):
                token += f";{k}={v}"
        offers.append(token)
    return header, offers


def diverges(left, right, header, offers):
    return left.best_match(header, list(offers)) != right.best_match(
        header, list(offers)
    )


def _simplify_entry(entry):
    """枚举一个客户端条目的简化版本：去 q、去参数。"""
    if ";" not in entry:
        return []
    head, _, tail = entry.partition(";")
    pieces = [p.strip() for p in tail.split(";")]
    variants = []
    if any(p.startswith("q=") for p in pieces):
        kept = [p for p in pieces if not p.startswith("q=")]
        variants.append(head + (";" + ";".join(kept) if kept else ""))
    if any(not p.startswith("q=") for p in pieces):
        kept = [p for p in pieces if p.startswith("q=")]
        variants.append(head + (";" + ";".join(kept) if kept else ""))
    return variants


def minimize(left, right, header, offers):
    """贪心缩小反例：逐条删客户端条目/服务端能力，并尝试简化
    单条目（去权重、去参数），直到任何进一步改动都不再触发分歧。
    """
    entries = [] if header is None else [e.strip() for e in header.split(",")]
    offers = list(offers)

    def check(es, os):
        h = None if header is None else ", ".join(es)
        return diverges(left, right, h, os)

    changed = True
    while changed:
        changed = False
        # 删除客户端条目
        for i in range(len(entries)):
            trial = entries[:i] + entries[i + 1 :]
            if check(trial, offers):
                entries = trial
                changed = True
                break
        if changed:
            continue
        # 删除服务端能力
        for i in range(len(offers)):
            trial = offers[:i] + offers[i + 1 :]
            if check(entries, trial):
                offers = trial
                changed = True
                break
        if changed:
            continue
        # 简化单个客户端条目
        for i, entry in enumerate(entries):
            for variant in _simplify_entry(entry):
                trial = entries[:i] + [variant] + entries[i + 1 :]
                if check(trial, offers):
                    entries = trial
                    changed = True
                    break
            if changed:
                break
    new_header = None if header is None else ", ".join(entries)
    return new_header, offers


def report(left_name, right_name, header, offers):
    left_result = content_negotiation.best_match(header, list(offers))
    right_module = (
        reference_impl
        if right_name == "reference"
        else __import__("buggy_variant")
    )
    right_result = right_module.best_match(header, list(offers))
    print("找到最小反例：")
    print(json.dumps(
        {
            "accept": header,
            "server_offers": list(offers),
            left_name + "_result": left_result,
            right_name + "_result": right_result,
        },
        ensure_ascii=False,
        indent=2,
    ))


def main():
    parser = argparse.ArgumentParser(description="内容协商对拍")
    parser.add_argument(
        "--impl",
        choices=["reference", "buggy"],
        default="reference",
        help="对拍对手：reference=参照实现；buggy=故意有缺陷的变体",
    )
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    right = reference_impl if args.impl == "reference" else __import__("buggy_variant")

    for n in range(args.iterations):
        header, offers = random_case(rng)
        if diverges(content_negotiation, right, header, offers):
            min_header, min_offers = minimize(
                content_negotiation, right, header, offers
            )
            report("main", args.impl, min_header, min_offers)
            print(f"\n（原始用例出现于第 {n + 1} 轮随机生成）")
            raise SystemExit(1)
    print(f"通过：{args.iterations} 个随机用例，主库与 {args.impl} 结果完全一致。")


if __name__ == "__main__":
    main()
