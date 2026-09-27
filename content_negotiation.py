"""HTTP 内容协商（Accept）打分与选择库，仅依赖标准库。

规则概要（完整说明见 RULES.md）：

1. 解析客户端 Accept 偏好列表，支持通配（*/*、type/*）、
   参数化类型（text/html;level=1）与权重 q。
2. 每条服务端能力的有效权重 = 所有能匹配它的客户端条目中
   「最具体」那条的 q 值（具体优先于通配）。
3. q=0 表示明确拒绝；所有能力都被拒绝时返回 None（对应 406）。
4. 全局决胜顺序：q 降序 -> 匹配条目具体度降序 ->
   客户端列表位置升序 -> 服务端列表位置升序。
5. 客户端未提供偏好（或全部无法解析）时，回退为服务端首选。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import List, Optional, Sequence, Tuple

__all__ = [
    "MediaRange",
    "MediaType",
    "parse_accept",
    "parse_media_type",
    "specificity",
    "matches",
    "best_match",
    "best_match_with_reason",
]

_TOKEN_EXTRA = set("!#$%&'*+-.^_`|~")


def _is_token(text: str) -> bool:
    return bool(text) and all(
        ch.isalnum() or ch in _TOKEN_EXTRA for ch in text
    )


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _parse_q(raw: str) -> Optional[Fraction]:
    """解析 q 值；非法或越界返回 None。"""
    try:
        q = Fraction(raw)
    except (ValueError, ZeroDivisionError):
        return None
    if q < 0 or q > 1:
        return None
    return q


def _split_params(item: str):
    """把 'a/b;k=v;...' 拆成主类型与参数列表；条目非法返回 None。

    同名参数重复出现时以最后一次为准（last-wins，与 dict 解析一致）。
    """
    parts = item.split(";")
    head = parts[0].strip()
    order: List[str] = []
    deduped = {}
    for chunk in parts[1:]:
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            return None
        name, _, value = chunk.partition("=")
        name = name.strip().lower()
        value = _unquote(value.strip())
        if not _is_token(name):
            return None
        if name not in deduped:
            order.append(name)
        deduped[name] = value
    return head, [(name, deduped[name]) for name in order]


@dataclass(frozen=True)
class MediaRange:
    """客户端偏好列表中的一个条目。"""

    type: str
    subtype: str
    params: Tuple[Tuple[str, str], ...]
    q: Fraction
    index: int  # 在客户端列表中的位置，从 0 开始


@dataclass(frozen=True)
class MediaType:
    """服务端能提供的一种表示。"""

    type: str
    subtype: str
    params: Tuple[Tuple[str, str], ...]
    index: int  # 在服务端列表中的位置，从 0 开始


def parse_accept(header: str) -> List[MediaRange]:
    """解析 Accept 头。无法解析的条目被丢弃；q 缺省为 1。

    q 之后出现的参数属于 accept-ext，不参与匹配，直接忽略。
    """
    ranges: List[MediaRange] = []
    for item in header.split(","):
        item = item.strip()
        if not item:
            continue
        parsed = _split_params(item)
        if parsed is None:
            continue
        head, raw_params = parsed
        if "/" not in head:
            continue
        type_, _, subtype = head.partition("/")
        type_ = type_.strip().lower()
        subtype = subtype.strip().lower()
        if not (_is_token(type_) or type_ == "*"):
            continue
        if not (_is_token(subtype) or subtype == "*"):
            continue
        if type_ == "*" and subtype != "*":
            continue  # '*/html' 之类的写法非法
        q = Fraction(1)
        params: List[Tuple[str, str]] = []
        valid = True
        seen_q = False
        for name, value in raw_params:
            if not seen_q and name == "q":
                parsed_q = _parse_q(value)
                if parsed_q is None:
                    valid = False
                    break
                q = parsed_q
                seen_q = True
            elif seen_q:
                continue  # accept-ext，忽略
            else:
                params.append((name, value))
        if not valid:
            continue
        ranges.append(
            MediaRange(type_, subtype, tuple(params), q, len(ranges))
        )
    return ranges


def parse_media_type(text: str, index: int = 0) -> Optional[MediaType]:
    """解析服务端能力描述，如 'text/html;level=1'。非法返回 None。"""
    parsed = _split_params(text.strip())
    if parsed is None:
        return None
    head, raw_params = parsed
    if "/" not in head:
        return None
    type_, _, subtype = head.partition("/")
    type_ = type_.strip().lower()
    subtype = subtype.strip().lower()
    if not _is_token(type_) or not _is_token(subtype):
        return None
    params: List[Tuple[str, str]] = []
    for name, value in raw_params:
        if name == "q":
            break  # 能力描述中的 q 及其后的 accept-ext 无意义，忽略
        params.append((name, value))
    return MediaType(type_, subtype, tuple(params), index)


def specificity(media_range: MediaRange) -> Tuple[int, int]:
    """具体度：(层级, 参数个数)。层级：精确=2，type/*=1，*/*=0。"""
    if media_range.type == "*":
        level = 0
    elif media_range.subtype == "*":
        level = 1
    else:
        level = 2
    return (level, len(media_range.params))


def matches(media_range: MediaRange, offer: MediaType) -> bool:
    """客户端条目是否匹配服务端能力：类型/子类型相容，且条目
    携带的每个参数都在能力中出现且取值相等。"""
    if media_range.type != "*" and media_range.type != offer.type:
        return False
    if media_range.subtype != "*" and media_range.subtype != offer.subtype:
        return False
    offer_params = dict(offer.params)
    return all(offer_params.get(k) == v for k, v in media_range.params)


def _effective_weight(
    offer: MediaType, ranges: Sequence[MediaRange]
) -> Optional[MediaRange]:
    """返回决定该能力有效权重的客户端条目（最具体者优先，
    并列时取客户端列表中靠前者）；无匹配返回 None。"""
    best: Optional[MediaRange] = None
    best_key: Optional[Tuple[int, int, int]] = None
    for media_range in ranges:
        if not matches(media_range, offer):
            continue
        level, nparams = specificity(media_range)
        key = (level, nparams, -media_range.index)
        if best_key is None or key > best_key:
            best_key = key
            best = media_range
    return best


def best_match(
    accept_header: Optional[str], offers: Sequence[str]
) -> Optional[str]:
    """在服务端能力列表中选出最合适的表示，返回其原始字符串；
    无可接受项返回 None。"""
    result = best_match_with_reason(accept_header, offers)
    return result[0] if result else None


def best_match_with_reason(
    accept_header: Optional[str], offers: Sequence[str]
) -> Optional[Tuple[str, Fraction, Optional[MediaRange]]]:
    """与 best_match 相同，但额外返回 (能力原文, 有效权重, 命中条目)。"""
    parsed_offers: List[Tuple[str, MediaType]] = []
    for position, raw in enumerate(offers):
        offer = parse_media_type(raw, position)
        if offer is not None:
            parsed_offers.append((raw, offer))
    if not parsed_offers:
        return None

    if accept_header is None or not accept_header.strip():
        return (parsed_offers[0][0], Fraction(1), None)

    ranges = parse_accept(accept_header)
    if not ranges:
        # 头部存在但没有任何可解析条目：视为未提供偏好
        return (parsed_offers[0][0], Fraction(1), None)

    scored = []
    for raw, offer in parsed_offers:
        decisive = _effective_weight(offer, ranges)
        if decisive is None or decisive.q <= 0:
            continue
        level, nparams = specificity(decisive)
        scored.append(
            (
                decisive.q,
                level,
                nparams,
                -decisive.index,
                -offer.index,
                raw,
                decisive,
            )
        )
    if not scored:
        return None
    scored.sort(key=lambda item: item[:5], reverse=True)
    top = scored[0]
    return (top[5], top[0], top[6])
