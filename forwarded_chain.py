"""forwarded_chain — 转发链解析与信任判定（仅标准库）。

核心思想
--------
X-Forwarded-For / X-Forwarded-Proto 等转发头由“客户端 -> 代理1 -> ... -> 本服务”
路径上的每一跳追加而成。客户端可以在请求里自带任意转发头，因此链的左侧永远
不可信。正确做法是：

1. 把直接 TCP 对端（peer）追加到链尾——它是唯一无法被伪造的一跳；
2. 自右向左扫描，跳过所有落在“可信代理网段”内的地址；
3. 遇到的第一个不可信地址即为真实客户端；
4. 它左侧的所有值一律丢弃并记录（spoof 候选）。

协议（proto）链没有地址可做网段判定，采用“按可信跳数对齐”的方式：
从右数跳过与 IP 链相同数量的可信代理跳，取到的值才是可信协议。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence, Union

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]

#: 转发头里常见的非地址占位符
_OPAQUE_TOKENS = {"unknown", "hidden", "_"}


@dataclass(frozen=True)
class Hop:
    """链上的一跳（已做端口剥离与地址规范化）。"""

    raw: str                      # 原始 token（未修改）
    ip: Optional[IPAddress]       # 规范化后的地址；解析失败为 None
    port: Optional[int] = None    # 显式端口（如有）
    valid: bool = True            # 是否解析成功
    error: Optional[str] = None   # 解析失败原因

    @property
    def text(self) -> Optional[str]:
        return str(self.ip) if self.ip is not None else None


@dataclass
class ChainResult:
    """解析结果。"""

    client_ip: Optional[str]           # 判定出的真实客户端地址
    client_port: Optional[int]         # 客户端跳上显式声明的端口（如有）
    peer_ip: str                       # 直接 TCP 对端
    trusted_hops: list[Hop] = field(default_factory=list)   # 自右向左跳过的可信代理
    dropped: list[Hop] = field(default_factory=list)        # 被丢弃的左侧值（伪造候选）
    spoofed: bool = False              # 是否检出了被丢弃的客户端侧值
    reason: str = ""

    def naive_leftmost(self) -> Optional[str]:
        """朴素解析（直接取链首）会得到的值，用于差异对比。"""
        for hop in self.dropped:
            if hop.valid and hop.ip is not None:
                return str(hop.ip)
        return self.client_ip


def parse_token(token: str) -> Hop:
    """解析单个转发链 token，支持：

    - ``1.2.3.4``            IPv4
    - ``1.2.3.4:5678``       IPv4 + 端口
    - ``::1`` / ``2001:db8::1``  裸 IPv6（无端口，冒号属于地址本身）
    - ``[2001:db8::1]``      方括号 IPv6
    - ``[2001:db8::1]:8443`` 方括号 IPv6 + 端口
    - ``unknown`` 等占位符 -> valid=False
    """
    token = token.strip()
    raw = token  # 记录去空格后的 token，便于审计日志直接比对
    if not token:
        return Hop(raw=raw, ip=None, valid=False, error="empty token")

    lowered = token.lower()
    if lowered in _OPAQUE_TOKENS:
        return Hop(raw=raw, ip=None, valid=False, error=f"opaque token {token!r}")

    host, port = token, None

    if token.startswith("["):
        # [v6] 或 [v6]:port
        end = token.find("]")
        if end == -1:
            return Hop(raw=raw, ip=None, valid=False, error="unbalanced '['")
        host = token[1:end]
        rest = token[end + 1:]
        if rest:
            if not rest.startswith(":"):
                return Hop(raw=raw, ip=None, valid=False,
                           error=f"unexpected suffix {rest!r} after ']'")
            port, err = _parse_port(rest[1:])
            if err:
                return Hop(raw=raw, ip=None, valid=False, error=err)
    elif token.count(":") == 1:
        # 形如 host:port —— 仅当 host 部分是合法 IPv4 时才按端口处理，
        # 否则整体交给 ipaddress 判定（避免把畸形 IPv6 误拆）。
        maybe_host, maybe_port = token.rsplit(":", 1)
        if _is_ipv4(maybe_host):
            host = maybe_host
            port, err = _parse_port(maybe_port)
            if err:
                return Hop(raw=raw, ip=None, valid=False, error=err)
        # 否则按无端口地址继续往下解析（大概率会报 invalid address）
    # token.count(":") >= 2 -> 裸 IPv6，无端口，直接整体解析

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return Hop(raw=raw, ip=None, valid=False, error=f"invalid address {host!r}")
    return Hop(raw=raw, ip=ip, port=port)


def _parse_port(text: str) -> tuple[Optional[int], Optional[str]]:
    if not text.isdigit():
        return None, f"invalid port {text!r}"
    port = int(text)
    if not (0 <= port <= 65535):
        return None, f"port {port} out of range"
    return port, None


def _is_ipv4(text: str) -> bool:
    try:
        ipaddress.IPv4Address(text)
        return True
    except ValueError:
        return False


def _normalize_networks(cidrs: Iterable[Union[str, IPNetwork]]) -> list[IPNetwork]:
    networks: list[IPNetwork] = []
    for c in cidrs:
        networks.append(c if isinstance(c, (ipaddress.IPv4Network, ipaddress.IPv6Network))
                        else ipaddress.ip_network(c, strict=False))
    return networks


class ForwardedChainParser:
    """按可信代理网段解析转发链。

    :param trusted_cidrs: 可信代理网段列表，如 ``["10.0.0.0/8", "fd00::/8"]``。
        直接 TCP 对端必须落在其中，转发头才会被采信。
    """

    def __init__(self, trusted_cidrs: Iterable[Union[str, IPNetwork]]):
        self.networks = _normalize_networks(trusted_cidrs)

    def is_trusted(self, ip: IPAddress) -> bool:
        return any(ip in net for net in self.networks)

    def resolve(self, header: Optional[str], peer_ip: str) -> ChainResult:
        """解析转发头并判定真实客户端。

        :param header: 转发头原始值（如 X-Forwarded-For），可为 None/空。
        :param peer_ip: 直接 TCP 对端地址（来自连接本身，不可伪造）。
        """
        peer_hop = parse_token(peer_ip)
        if not peer_hop.valid or peer_hop.ip is None:
            # 对端地址本身异常：无法建立信任锚点，全部丢弃
            dropped = [parse_token(t) for t in (header or "").split(",") if t.strip()]
            return ChainResult(
                client_ip=None, client_port=None, peer_ip=peer_ip,
                dropped=dropped, spoofed=bool(dropped),
                reason=f"peer address {peer_ip!r} is invalid; nothing can be trusted",
            )

        hops: list[Hop] = []
        if header:
            hops = [parse_token(t) for t in header.split(",") if t.strip()]

        if not self.is_trusted(peer_hop.ip):
            # 对端不在可信网段：客户端可完全伪造转发头，整链丢弃
            return ChainResult(
                client_ip=str(peer_hop.ip), client_port=None,
                peer_ip=str(peer_hop.ip), dropped=hops, spoofed=bool(hops),
                reason="peer is not a trusted proxy; entire header discarded",
            )

        # 自右向左：对端可信，继续跳过可信代理，直到第一个不可信跳
        chain = hops + [peer_hop]
        idx = len(chain) - 1
        trusted: list[Hop] = []
        while idx >= 0:
            hop = chain[idx]
            if hop.valid and hop.ip is not None and self.is_trusted(hop.ip):
                trusted.append(hop)
                idx -= 1
            else:
                break
        trusted.reverse()  # 恢复从左到右的顺序，便于阅读

        if idx < 0:
            # 整条链（含对端）全部可信：没有留下任何客户端候选
            return ChainResult(
                client_ip=None, client_port=None, peer_ip=str(peer_hop.ip),
                trusted_hops=trusted, dropped=[], spoofed=False,
                reason="every hop is trusted; no client address remains "
                       "(header was empty or fully generated by trusted proxies)",
            )

        client_hop = chain[idx]
        dropped = chain[:idx]
        if client_hop.valid and client_hop.ip is not None:
            return ChainResult(
                client_ip=str(client_hop.ip), client_port=client_hop.port,
                peer_ip=str(peer_hop.ip), trusted_hops=trusted, dropped=dropped,
                spoofed=bool(dropped),
                reason="first untrusted hop from the right is the client",
            )
        # 客户端候选本身解析失败（畸形值/占位符）：不可信但取不出地址
        return ChainResult(
            client_ip=None, client_port=None, peer_ip=str(peer_hop.ip),
            trusted_hops=trusted, dropped=dropped, spoofed=bool(dropped),
            reason=f"client candidate {client_hop.raw!r} is malformed: {client_hop.error}",
        )

    def resolve_proto(self, header: Optional[str], num_trusted: int,
                      default: Optional[str] = None) -> tuple[Optional[str], list[str]]:
        """按可信跳数解析协议链（如 X-Forwarded-Proto）。

        协议值没有网段可判，只能与 IP 链“对齐”：``num_trusted`` 为可信代理
        跳数（含直接对端，即 ``len(result.trusted_hops)``），每个可信代理
        会追加一个协议值，因此从右数跳过该数量后，剩下的最右值即客户端侧
        协议；其左侧全部丢弃。

        :return: ``(proto, dropped_values)``；链不够长或为空时返回 ``default``。
        """
        values = [v.strip().lower() for v in (header or "").split(",") if v.strip()]
        if not values:
            return default, []
        if num_trusted <= 0:
            # 没有任何可信代理（含对端不可信）：整链可伪造
            return default, values
        idx = len(values) - num_trusted
        if idx < 0:
            # 可信代理跳数比链还长：链整体由可信代理生成，无客户端侧值
            return default, []
        return values[idx], values[:idx]
