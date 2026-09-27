"""可信转发链解析库（仅标准库）。

从 X-Forwarded-For / RFC 7239 Forwarded 中，按可信代理网段
自右向左确定真实客户端地址与协议；不可信来源之前的值一律
丢弃并记录，用于限流、审计与伪造检出。

信任模型：
  1. TCP 直连对端 peer 必须在可信网段内，否则整个转发头都是
     客户端可控的，全部丢弃，真实来源就是 peer 本身。
  2. peer 可信时，从链尾（离本服务最近、由可信入口追加）向
     链首遍历：可信代理跳继续向左，遇到第一个不可信或非法的
     跳即为真实客户端位置，其左侧所有值视为客户端伪造，丢弃。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence, Union

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

# 默认可信网段：常见内网/回环/链路本地，生产环境应显式传入
DEFAULT_TRUSTED_NETWORKS: Sequence[str] = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
)

KNOWN_PROTOCOLS = frozenset({"http", "https"})

_BRACKET_RE = re.compile(r"^\[(?P<addr>[^\]]+)\](?::(?P<port>[^:]*))?$")


@dataclass(frozen=True)
class Hop:
    """链中一跳。valid 为 False 时 error 说明原因。"""

    raw: str
    ip: Optional[IPAddress] = None
    port: Optional[int] = None
    error: Optional[str] = None

    @property
    def valid(self) -> bool:
        return self.ip is not None and self.error is None

    def __str__(self) -> str:
        if not self.valid:
            return f"{self.raw!r}(非法:{self.error})"
        return str(self.ip) if self.port is None else f"{self.ip}:{self.port}"


def _parse_port(text: Optional[str]):
    """返回 (port, error)。text 为 None 表示无端口。"""
    if text is None:
        return None, None
    if not text.isdigit():
        return None, f"端口非法: {text!r}"
    port = int(text)
    if not 0 <= port <= 65535:
        return None, f"端口越界: {port}"
    return port, None


def parse_hop(token: str) -> Hop:
    """解析单个转发值，支持：

    - IPv4:            203.0.113.7
    - IPv4 + 端口:     203.0.113.7:8080
    - IPv6（裸写）:    2001:db8::1        （裸 IPv6 不允许带端口）
    - IPv6（方括号）:  [2001:db8::1]
    - IPv6 + 端口:     [2001:db8::1]:443  （带端口必须加方括号）
    """
    raw = token
    token = token.strip()
    if not token:
        return Hop(raw=raw, error="空值")
    lowered = token.lower()
    if lowered == "unknown":
        return Hop(raw=raw, error="unknown 占位符")
    if token.startswith("_"):
        return Hop(raw=raw, error="混淆标识符（obfuscated identifier）")

    bracket = _BRACKET_RE.match(token)
    if bracket:
        try:
            ip = ipaddress.ip_address(bracket.group("addr"))
        except ValueError:
            return Hop(raw=raw, error="方括号内不是合法 IP")
        port, err = _parse_port(bracket.group("port"))
        if err:
            return Hop(raw=raw, error=err)
        return Hop(raw=raw, ip=ip, port=port)

    if token.count(":") == 1:
        # host:port 形式（仅 IPv4 或主机名；IPv6 带端口必须走方括号）
        addr, port_text = token.rsplit(":", 1)
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return Hop(raw=raw, error="不是合法 IP 地址")
        port, err = _parse_port(port_text)
        if err:
            return Hop(raw=raw, error=err)
        return Hop(raw=raw, ip=ip, port=port)

    # 裸地址：IPv4 或无端口 IPv6（如 2001:db8::1，含 ::1:8080 这类
    # 合法 IPv6 写法，一律按地址解释，不猜端口）
    try:
        return Hop(raw=raw, ip=ipaddress.ip_address(token))
    except ValueError:
        return Hop(raw=raw, error="不是合法 IP 地址")


def parse_xff(header: str) -> list:
    """解析 X-Forwarded-For：逗号分隔多值，逐项 strip。"""
    return [parse_hop(part) for part in header.split(",")]


def _split_quoted(text: str, sep: str) -> list:
    """按分隔符拆分，忽略双引号内的分隔符（RFC 7239 需要）。"""
    parts, buf, in_quote, escaped = [], [], False, False
    for ch in text:
        if escaped:
            buf.append(ch)
            escaped = False
        elif ch == "\\" and in_quote:
            buf.append(ch)
            escaped = True
        elif ch == '"':
            buf.append(ch)
            in_quote = not in_quote
        elif ch == sep and not in_quote:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _unquote(value: str) -> str:
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


@dataclass(frozen=True)
class ForwardedElement:
    """RFC 7239 Forwarded 头的一个元素。"""

    for_hop: Hop
    proto: Optional[str]
    raw: str


def parse_forwarded(header: str) -> list:
    """解析 RFC 7239 Forwarded 头，提取 for= 与 proto=。"""
    elements = []
    for element in _split_quoted(header, ","):
        params = {}
        for pair in _split_quoted(element, ";"):
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            params[key.strip().lower()] = _unquote(value.strip())
        for_hop = parse_hop(params.get("for", ""))
        proto = params.get("proto")
        proto = proto.lower() if proto else None
        elements.append(ForwardedElement(for_hop=for_hop, proto=proto, raw=element.strip()))
    return elements


@dataclass
class Resolution:
    """解析结果。"""

    client_ip: Optional[str]
    client_port: Optional[int]
    protocol: Optional[str]
    peer: str
    peer_trusted: bool
    source: str                      # forwarded / x-forwarded-for / peer / none
    chain: list = field(default_factory=list)        # 全部跳（左→右）
    trusted_hops: list = field(default_factory=list)  # 右侧可信代理跳
    discarded: list = field(default_factory=list)     # 被丢弃的左侧值（疑似伪造）
    anomalies: list = field(default_factory=list)
    spoof_detected: bool = False


def _compile_networks(networks: Sequence[str]):
    return [ipaddress.ip_network(n) for n in networks]


def _in_trusted(ip: IPAddress, networks) -> bool:
    return any(ip.version == net.version and ip in net for net in networks)


def _pick_header(headers: Optional[Mapping], name: str) -> Optional[str]:
    if not headers:
        return None
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def resolve_client(
    peer: str,
    *,
    headers: Optional[Mapping] = None,
    x_forwarded_for: Optional[str] = None,
    x_forwarded_proto: Optional[str] = None,
    forwarded: Optional[str] = None,
    trusted_networks: Sequence[str] = DEFAULT_TRUSTED_NETWORKS,
) -> Resolution:
    """按可信链解析真实客户端地址与协议。

    peer: TCP 直连对端地址（来自 socket，不可伪造）。
    headers: 可选的请求头映射（大小写不敏感）；显式参数优先。
    trusted_networks: 可信代理网段（CIDR）。
    """
    if x_forwarded_for is None:
        x_forwarded_for = _pick_header(headers, "x-forwarded-for")
    if x_forwarded_proto is None:
        x_forwarded_proto = _pick_header(headers, "x-forwarded-proto")
    if forwarded is None:
        forwarded = _pick_header(headers, "forwarded")

    networks = _compile_networks(trusted_networks)

    peer_hop = parse_hop(peer)
    if not peer_hop.valid:
        raise ValueError(f"peer 地址非法: {peer!r} ({peer_hop.error})")
    peer_trusted = _in_trusted(peer_hop.ip, networks)

    # 选择链来源：Forwarded 优先于 X-Forwarded-For
    elements = None
    if forwarded and forwarded.strip():
        elements = parse_forwarded(forwarded)
        hops = [el.for_hop for el in elements]
        source = "forwarded"
    elif x_forwarded_for and x_forwarded_for.strip():
        hops = parse_xff(x_forwarded_for)
        source = "x-forwarded-for"
    else:
        hops = []
        source = "none"

    result = Resolution(
        client_ip=None,
        client_port=None,
        protocol=None,
        peer=str(peer_hop.ip),
        peer_trusted=peer_trusted,
        source=source,
        chain=hops,
    )

    # 情形 1：直连对端不可信 —— 转发头整体是客户端伪造的
    if not peer_trusted:
        result.client_ip = str(peer_hop.ip)
        result.client_port = peer_hop.port
        result.source = "peer"
        result.discarded = list(hops)
        result.spoof_detected = bool(hops)
        if hops:
            result.anomalies.append(
                "直连对端不在可信网段，转发头整体视为客户端伪造并丢弃"
            )
        return result

    # 情形 2：peer 可信但没有转发头 —— 无法回溯源
    if not hops:
        result.anomalies.append("可信代理未提供转发头，无法确定真实客户端")
        return result

    # 情形 3：自右向左走信任链
    idx = len(hops) - 1
    while idx >= 0 and hops[idx].valid and _in_trusted(hops[idx].ip, networks):
        result.trusted_hops.insert(0, hops[idx])
        idx -= 1

    if idx < 0:
        result.anomalies.append("链中全部为可信代理，真实客户端值缺失（链耗尽）")
    elif not hops[idx].valid:
        result.anomalies.append(
            f"客户端位置的值非法，已截断: {hops[idx].raw!r} ({hops[idx].error})"
        )
    else:
        result.client_ip = str(hops[idx].ip)
        result.client_port = hops[idx].port

    # 客户端位置左侧的所有值都是客户端自己写的，丢弃并记录
    result.discarded = hops[:idx] if idx >= 0 else []
    result.spoof_detected = bool(result.discarded)

    # 协议判定：只采信可信边界记录的值
    if elements is not None:
        proto = elements[idx].proto if idx >= 0 else None
    elif x_forwarded_proto and x_forwarded_proto.strip():
        values = [v.strip().lower() for v in x_forwarded_proto.split(",")]
        # 与 XFF 链按位置对齐；越界时取最右（可信入口追加的）
        proto = values[min(max(idx, 0), len(values) - 1)]
    else:
        proto = None
    if proto:
        if proto in KNOWN_PROTOCOLS:
            result.protocol = proto
        else:
            result.anomalies.append(f"协议值非法，已忽略: {proto!r}")

    return result


def naive_resolve(
    peer: str,
    *,
    x_forwarded_for: Optional[str] = None,
    x_forwarded_proto: Optional[str] = None,
) -> dict:
    """朴素解析（取链首），仅用于对比演示其不安全性。"""
    client = peer
    if x_forwarded_for and x_forwarded_for.strip():
        client = x_forwarded_for.split(",")[0].strip()
    proto = None
    if x_forwarded_proto and x_forwarded_proto.strip():
        proto = x_forwarded_proto.split(",")[0].strip().lower()
    return {"client_ip": client, "protocol": proto}
