"""故意带缺陷的协商实现：每条能力的有效权重取「客户端列表中
第一个能匹配的条目」的 q，而不是「最具体的匹配条目」的 q。

这是对拍脚本 duel.py 的演示对象：当主库/参照实现与它产生分歧时，
duel.py 会自动缩小并输出最小反例（见 COUNTEREXAMPLE.md）。
"""

from __future__ import annotations

from reference_impl import _match, _parse_entry, _parse_offer


def best_match(accept_header, offers):
    parsed_offers = []
    for position, raw in enumerate(offers):
        offer = _parse_offer(raw)
        if offer is not None:
            parsed_offers.append((position, raw, offer))
    if not parsed_offers:
        return None
    if accept_header is None or not accept_header.strip():
        return parsed_offers[0][1]

    entries = []
    for item in accept_header.split(","):
        item = item.strip()
        if not item:
            continue
        entry = _parse_entry(item)
        if entry is not None:
            entries.append(entry)
    if not entries:
        return parsed_offers[0][1]

    best = None  # (q, server_index, raw)
    for server_index, raw, offer in parsed_offers:
        q = None
        for entry in entries:  # 缺陷所在：先到先得，不看具体度
            if _match(entry, offer):
                q = entry[3]
                break
        if q is None or q <= 0:
            continue
        if best is None or q > best[0]:
            best = (q, server_index, raw)
    return best[2] if best else None
