"""内容协商参照实现：刻意用最朴素、最直接的写法实现 RULES.md
中的同一套规则，与 content_negotiation.py 不共享任何代码，
用于对拍（duel.py）。

返回值约定与主库一致：选中的服务端能力原始字符串，或 None。
"""

from __future__ import annotations

from fractions import Fraction
from functools import cmp_to_key


def _parse_entry(item):
    """解析单个 'a/b;k=v;q=0.8' 条目。

    返回 (type, subtype, params_dict, q) 或 None（非法条目）。
    """
    parts = [p.strip() for p in item.split(";")]
    head = parts[0]
    if "/" not in head:
        return None
    type_, subtype = (s.strip().lower() for s in head.split("/", 1))
    for piece in (type_, subtype):
        if piece != "*" and (
            not piece
            or not all(c.isalnum() or c in "!#$%&'*+-.^_`|~" for c in piece)
        ):
            return None
    if type_ == "*" and subtype != "*":
        return None
    params = {}
    q = Fraction(1)
    seen_q = False
    for chunk in parts[1:]:
        if not chunk:
            continue
        if "=" not in chunk:
            return None
        name, _, value = chunk.partition("=")
        name = name.strip().lower()
        value = value.strip()
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if not name or not all(
            c.isalnum() or c in "!#$%&'*+-.^_`|~" for c in name
        ):
            return None
        if not seen_q and name == "q":
            try:
                q = Fraction(value)
            except (ValueError, ZeroDivisionError):
                return None
            if q < 0 or q > 1:
                return None
            seen_q = True
        elif not seen_q:
            params[name] = value
        # q 之后的参数是 accept-ext，忽略
    return (type_, subtype, params, q)


def _parse_offer(text):
    entry = _parse_entry(text)
    if entry is None:
        return None
    type_, subtype, params, _q = entry
    if type_ == "*" or subtype == "*":
        return None  # 服务端能力必须是具体类型
    params.pop("q", None)
    return (type_, subtype, params)


def _level(entry):
    type_, subtype, _params, _q = entry
    if type_ == "*":
        return 0
    if subtype == "*":
        return 1
    return 2


def _match(entry, offer):
    etype, esub, eparams, _q = entry
    otype, osub, oparams = offer
    if etype != "*" and etype != otype:
        return False
    if esub != "*" and esub != osub:
        return False
    for key, value in eparams.items():
        if oparams.get(key) != value:
            return False
    return True


def best_match(accept_header, offers):
    # 第一步：解析服务端能力，丢弃非法项
    parsed_offers = []
    for position, raw in enumerate(offers):
        offer = _parse_offer(raw)
        if offer is not None:
            parsed_offers.append((position, raw, offer))
    if not parsed_offers:
        return None

    # 第二步：客户端未提供偏好 -> 服务端首选
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

    # 第三步：逐条能力计算有效权重
    # 候选元组：(q, level, nparams, client_index, server_index, raw)
    candidates = []
    for server_index, raw, offer in parsed_offers:
        decisive = None  # (level, nparams, client_index, q)
        for client_index, entry in enumerate(entries):
            if not _match(entry, offer):
                continue
            cand = (_level(entry), len(entry[2]), client_index, entry[3])
            if decisive is None:
                decisive = cand
                continue
            # 具体度更高者胜；并列取客户端列表中靠前者
            if (cand[0], cand[1]) > (decisive[0], decisive[1]):
                decisive = cand
        if decisive is None:
            continue
        q = decisive[3]
        if q <= 0:
            continue  # 明确拒绝
        candidates.append(
            (q, decisive[0], decisive[1], decisive[2], server_index, raw)
        )

    if not candidates:
        return None

    # 第四步：全局决胜
    # q 降序 -> 具体度降序 -> 客户端位置升序 -> 服务端位置升序
    def compare(a, b):
        if a[0] != b[0]:
            return -1 if a[0] > b[0] else 1
        if (a[1], a[2]) != (b[1], b[2]):
            return -1 if (a[1], a[2]) > (b[1], b[2]) else 1
        if a[3] != b[3]:
            return -1 if a[3] < b[3] else 1
        if a[4] != b[4]:
            return -1 if a[4] < b[4] else 1
        return 0

    candidates.sort(key=cmp_to_key(compare))
    return candidates[0][5]
