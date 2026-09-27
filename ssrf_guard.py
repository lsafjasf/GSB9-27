"""ssrf_guard — 外呼地址白名单校验库（SSRF 防护），仅依赖 Python 3 标准库。

防护模型：
1. 解析 URL 后先做结构性校验（scheme / userinfo / 端口 / 非法字符）。
2. 主机名为 IP 字面量时直接判定；否则先过域名白名单，再做 DNS 解析，
   对解析得到的每一个 IP 做地址段判定（内网/回环/链路本地/保留段一律拒绝）。
3. 校验与连接一致（防 DNS 重绑定/TOCTOU）：解析一次，连接时直接使用
   已校验过的 IP，不再二次解析；重定向每一跳都重新完整校验。
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
import threading
import time
from collections import namedtuple
from urllib.parse import urljoin, urlsplit

__all__ = [
    "SsrfBlocked",
    "ResolutionTimeout",
    "OutboundGuard",
    "ValidatedTarget",
    "BLOCKED_V4",
    "BLOCKED_V6",
]


class SsrfBlocked(Exception):
    """目标地址被白名单策略拒绝。"""


class ResolutionTimeout(SsrfBlocked):
    """DNS 解析超时（按失败关闭处理）。"""


# (CIDR, 判定依据)。任何解析结果命中以下网段一律拒绝。
BLOCKED_V4 = [
    ("0.0.0.0/8", "本机/未指定地址 (RFC 791)"),
    ("10.0.0.0/8", "私有地址 (RFC 1918)"),
    ("100.64.0.0/10", "运营商级 NAT 共享地址 (RFC 6598)"),
    ("127.0.0.0/8", "回环地址 (RFC 1122)"),
    ("169.254.0.0/16", "链路本地；含云元数据 169.254.169.254 (RFC 3927)"),
    ("172.16.0.0/12", "私有地址 (RFC 1918)"),
    ("192.0.0.0/24", "IETF 协议分配 (RFC 6890)"),
    ("192.0.2.0/24", "文档示例 TEST-NET-1 (RFC 5737)"),
    ("192.168.0.0/16", "私有地址 (RFC 1918)"),
    ("198.18.0.0/15", "网络基准测试 (RFC 2544)"),
    ("198.51.100.0/24", "文档示例 TEST-NET-2 (RFC 5737)"),
    ("203.0.113.0/24", "文档示例 TEST-NET-3 (RFC 5737)"),
    ("224.0.0.0/4", "组播 (RFC 5771)"),
    ("240.0.0.0/4", "保留地址段 (RFC 1112)"),
]

BLOCKED_V6 = [
    ("::/128", "未指定地址 (RFC 4291)"),
    ("::1/128", "回环地址 (RFC 4291)"),
    ("::ffff:0:0/96", "IPv4 映射地址，按映射后的 IPv4 判定 (RFC 4291)"),
    ("64:ff9b::/96", "NAT64 周知前缀，可能映射内网 IPv4 (RFC 6052)"),
    ("100::/64", "丢弃前缀 (RFC 6666)"),
    ("2001::/23", "IETF 特殊用途（Teredo/基准测试等）(RFC 6890)"),
    ("2001:db8::/32", "文档示例 (RFC 3849)"),
    ("fc00::/7", "唯一本地地址 ULA (RFC 4193)"),
    ("fe80::/10", "链路本地 (RFC 4291)"),
    ("ff00::/8", "组播 (RFC 4291)"),
]

_V4_NETWORKS = [(ipaddress.IPv4Network(c), why) for c, why in BLOCKED_V4]
_V6_NETWORKS = [(ipaddress.IPv6Network(c), why) for c, why in BLOCKED_V6]

ValidatedTarget = namedtuple(
    "ValidatedTarget", ["url", "scheme", "host", "port", "ips"]
)

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _system_resolver(host, port):
    """默认解析器：getaddrinfo，返回去重后的 IP 字符串列表。"""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips = []
    for info in infos:
        ip = info[4][0]
        if ip not in ips:
            ips.append(ip)
    return ips


def parse_ip_literal(host):
    """把主机名按 IP 字面量解析，覆盖特殊数字写法；非字面量返回 None。

    覆盖：点分十进制、纯十进制整数、十六进制、八进制、少于 4 段的简写、
    IPv6（含 ::ffff: 映射、链路本地 zone id）。数字写法解析依赖 C 库
    inet_aton，与绝大多数网络组件的解释保持一致。
    """
    h = host.strip().rstrip(".")
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if "%" in h:  # 链路本地 zone id，如 fe80::1%eth0 / fe80::1%25eth0
        h = h.split("%", 1)[0]
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    if ":" in h:
        return None
    try:
        return ipaddress.IPv4Address(socket.inet_aton(h))
    except OSError:
        return None


class OutboundGuard:
    """外呼白名单校验器。

    参数：
        allowed_hosts: 域名白名单（None 表示不限制域名，仅做地址段过滤）。
                       按精确匹配或 ".后缀" 边界匹配。
        allowed_cidrs: 目标 IP 必须落入的网段白名单（None 表示不限制）。
        allowed_ports: 允许的端口集合。
        schemes:       允许的 URL scheme。
        dns_timeout:   单次 DNS 解析超时秒数，超时按拒绝处理。
        resolver:      可注入的解析器 resolver(host, port) -> [ip, ...]，
                       便于测试或接入内部递归 DNS。
    """

    def __init__(
        self,
        allowed_hosts=None,
        allowed_cidrs=None,
        allowed_ports=(80, 443),
        schemes=("http", "https"),
        dns_timeout=5.0,
        resolver=None,
    ):
        self._allowed_hosts = (
            {h.lower().rstrip(".") for h in allowed_hosts}
            if allowed_hosts is not None
            else None
        )
        self._allowed_cidrs = (
            [ipaddress.ip_network(c) for c in allowed_cidrs]
            if allowed_cidrs is not None
            else None
        )
        self._allowed_ports = set(allowed_ports)
        self._schemes = {s.lower() for s in schemes}
        self._dns_timeout = dns_timeout
        self._resolver = resolver or _system_resolver

    # ---------- 地址段判定 ----------

    def validate_ip(self, ip):
        """对单个 IP 做地址段判定，命中黑名单/不在白名单则抛 SsrfBlocked。"""
        if isinstance(ip, str):
            ip = ipaddress.ip_address(ip)

        if isinstance(ip, ipaddress.IPv6Address):
            mapped = ip.ipv4_mapped
            if mapped is not None:  # ::ffff:a.b.c.d 按 IPv4 判定
                self._check_blocked(mapped)
                self._check_allowed_cidrs(mapped)
                return
            if int(ip) >> 112 == 0x2002:  # 6to4: 2002::/16 内嵌 IPv4
                embedded = ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
                self._check_blocked(embedded)
            self._check_blocked(ip)
            self._check_allowed_cidrs(ip)
            return

        self._check_blocked(ip)
        self._check_allowed_cidrs(ip)

    def _check_blocked(self, ip):
        networks = _V4_NETWORKS if ip.version == 4 else _V6_NETWORKS
        for net, why in networks:
            if ip in net:
                raise SsrfBlocked(
                    f"目标 {ip} 命中禁止网段 {net.with_prefixlen}（{why}）"
                )

    def _check_allowed_cidrs(self, ip):
        if self._allowed_cidrs is None:
            return
        for net in self._allowed_cidrs:
            if ip.version == net.version and ip in net:
                return
        raise SsrfBlocked(f"目标 {ip} 不在允许的 IP 白名单网段内")

    # ---------- 域名白名单 ----------

    def _check_allowlist(self, host):
        if self._allowed_hosts is None:
            return
        for allowed in self._allowed_hosts:
            if host == allowed or host.endswith("." + allowed):
                return
        raise SsrfBlocked(f"域名 {host} 不在白名单内")

    # ---------- DNS 解析（带超时，失败关闭） ----------

    def _resolve(self, host, port):
        box = {}

        def run():
            try:
                box["ips"] = self._resolver(host, port)
            except Exception as exc:  # 解析失败一律拒绝
                box["err"] = exc

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(self._dns_timeout)
        if t.is_alive():
            raise ResolutionTimeout(
                f"DNS 解析 {host} 超过 {self._dns_timeout}s，按拒绝处理"
            )
        if "err" in box:
            raise SsrfBlocked(f"DNS 解析 {host} 失败: {box['err']}")
        ips = list(dict.fromkeys(box.get("ips") or []))
        if not ips:
            raise SsrfBlocked(f"DNS 解析 {host} 无结果")
        return ips

    # ---------- URL 校验主入口 ----------

    def validate_url(self, url):
        """校验 URL，返回 ValidatedTarget；任何一环不通过即抛 SsrfBlocked。"""
        if not isinstance(url, str) or not url or len(url) > 2048:
            raise SsrfBlocked("URL 为空或超长")
        if any(ord(c) < 0x21 or ord(c) == 0x7F for c in url):
            raise SsrfBlocked("URL 含空白/控制字符")
        if "\\" in url:
            raise SsrfBlocked("URL 含反斜杠，存在解析歧义")

        try:
            parts = urlsplit(url)
        except ValueError as exc:
            raise SsrfBlocked(f"URL 解析失败: {exc}") from None

        scheme = parts.scheme.lower()
        if scheme not in self._schemes:
            raise SsrfBlocked(f"scheme {scheme!r} 不被允许")

        # 用户名欺骗：user@host / user:pass@host 一律拒绝
        if parts.username is not None or parts.password is not None:
            raise SsrfBlocked("URL 不允许携带 userinfo（user@host 欺骗风险）")

        host = parts.hostname
        if not host:
            raise SsrfBlocked("URL 缺少主机名")
        host = host.rstrip(".").lower()

        try:
            port = parts.port
        except ValueError:
            raise SsrfBlocked("端口非法") from None
        if port is None:
            port = _DEFAULT_PORTS.get(scheme)
        if port not in self._allowed_ports:
            raise SsrfBlocked(f"端口 {port} 不在允许集合内")

        literal = parse_ip_literal(host)
        if literal is not None:
            self.validate_ip(literal)
            ips = [str(literal)]
        else:
            self._check_allowlist(host)
            ips = self._resolve(host, port)
            for ip in ips:  # 解析结果逐条判定，任一命中即拒绝
                self.validate_ip(ip)

        return ValidatedTarget(url=url, scheme=scheme, host=host, port=port, ips=ips)

    # ---------- 校验与连接一致（防 DNS 重绑定 / TOCTOU） ----------

    def open_connection(self, url, connect_timeout=5.0, connector=None):
        """解析并校验后，直接连接已校验的 IP（不再二次解析）。

        connector(address, timeout) 可注入，默认 socket.create_connection。
        HTTPS 使用原始主机名做 SNI/证书校验，业务侧需使用原始 Host 头。
        """
        target = self.validate_url(url)
        connector = connector or socket.create_connection
        last_err = None
        for ip in target.ips:
            try:
                sock = connector((ip, target.port), connect_timeout)
            except OSError as exc:
                last_err = exc
                continue
            if target.scheme == "https":
                ctx = ssl.create_default_context()
                sock = ctx.wrap_socket(sock, server_hostname=target.host)
            return sock, target
        raise SsrfBlocked(f"连接已校验地址失败: {last_err}")

    def verify_stable_resolution(self, host, port=443, checks=3, interval=0.3):
        """重绑定检测：多次解析结果必须一致且全部合法，否则拒绝。"""
        first = None
        for i in range(checks):
            ips = set(self._resolve(host, port))
            for ip in ips:
                self.validate_ip(ip)
            if first is None:
                first = ips
            elif ips != first:
                raise SsrfBlocked(
                    f"DNS 重绑定迹象：{host} 多次解析结果不一致 {first} vs {ips}"
                )
            if i + 1 < checks:
                time.sleep(interval)
        return sorted(first)

    # ---------- 带重定向校验的抓取 ----------

    def fetch(self, url, max_redirects=5, http_get=None, connect_timeout=5.0):
        """抓取 URL；每一跳重定向都重新完整校验。

        http_get(url, target) -> (status, headers, body) 可注入 mock；
        默认使用内置的最小 HTTP 客户端（连接已校验 IP）。
        """
        current = url
        for _ in range(max_redirects + 1):
            target = self.validate_url(current)
            if http_get is not None:
                status, headers, body = http_get(current, target)
            else:
                status, headers, body = self._real_get(target, connect_timeout)
            if status in (301, 302, 303, 307, 308):
                location = headers.get("location")
                if not location:
                    raise SsrfBlocked(f"重定向响应 {status} 缺少 Location")
                current = urljoin(current, location)
                continue
            return status, headers, body
        raise SsrfBlocked(f"重定向次数超过 {max_redirects}")

    def _real_get(self, target, connect_timeout, read_limit=1_000_000):
        sock, _ = self.open_connection(target.url, connect_timeout=connect_timeout)
        try:
            parts = urlsplit(target.url)
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            req = (
                f"GET {path} HTTP/1.1\r\nHost: {target.host}\r\n"
                f"Connection: close\r\nUser-Agent: ssrf-guard/1.0\r\n\r\n"
            )
            sock.sendall(req.encode())
            data = b""
            while len(data) < read_limit:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        finally:
            sock.close()
        head, _, body = data.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        status = int(lines[0].split()[1])
        headers = {}
        for line in lines[1:]:
            key, _, value = line.partition(b":")
            headers[key.strip().lower().decode()] = value.strip().decode()
        return status, headers, body
