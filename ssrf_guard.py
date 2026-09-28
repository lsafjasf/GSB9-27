"""ssrf_guard — 外呼目标地址白名单校验库（仅标准库）。

防护目标：服务端请求伪造（SSRF）。在外呼（webhook / 回调 / 拉取）场景下，
对用户提交的 URL 在「解析出目标地址之后、发起连接之前」做强制校验，
并通过 IP 钉扎（pinning）保证「校验的地址」与「实际连接的地址」一致。

用法：
    from ssrf_guard import validate_url, safe_fetch

    r = validate_url("http://example.com/cb")
    if not r.ok:
        ...拒绝...

    resp = safe_fetch("http://example.com/cb")   # 自动逐跳校验重定向
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Callable, NamedTuple, Optional, Sequence
from urllib.parse import urljoin, urlsplit

# ---------------------------------------------------------------------------
# 封禁地址段清单（判定依据见 README「地址段清单」）
# ---------------------------------------------------------------------------

BLOCKED_V4 = [
    ("0.0.0.0/8",       "本机/未指定地址（RFC 791）"),
    ("10.0.0.0/8",      "私有地址（RFC 1918）"),
    ("100.64.0.0/10",   "运营商级 NAT 共享地址（RFC 6598）"),
    ("127.0.0.0/8",     "回环地址（RFC 1122）"),
    ("169.254.0.0/16",  "链路本地地址，含云元数据 169.254.169.254（RFC 3927）"),
    ("172.16.0.0/12",   "私有地址（RFC 1918）"),
    ("192.0.0.0/24",    "IETF 协议分配（RFC 6890）"),
    ("192.0.2.0/24",    "文档示例 TEST-NET-1（RFC 5737）"),
    ("192.168.0.0/16",  "私有地址（RFC 1918）"),
    ("198.18.0.0/15",   "网络基准测试地址（RFC 2544）"),
    ("198.51.100.0/24", "文档示例 TEST-NET-2（RFC 5737）"),
    ("203.0.113.0/24",  "文档示例 TEST-NET-3（RFC 5737)"),
    ("224.0.0.0/4",     "组播地址（RFC 5771）"),
    ("240.0.0.0/4",     "保留地址（RFC 1112）"),
]

BLOCKED_V6 = [
    ("::/128",          "未指定地址（RFC 4291）"),
    ("::1/128",         "回环地址（RFC 4291）"),
    ("::ffff:0:0/96",   "IPv4 映射地址，整体封禁（RFC 4291）"),
    ("::/96",           "IPv4 兼容地址（已废弃，RFC 4291）"),
    ("64:ff9b::/96",    "NAT64 过渡地址，内嵌 IPv4（RFC 6052）"),
    ("100::/64",        "仅丢弃前缀（RFC 6666）"),
    ("2001::/32",       "Teredo 过渡地址，内嵌 IPv4（RFC 4380）"),
    ("2001:db8::/32",   "文档示例前缀（RFC 3849）"),
    ("2002::/16",       "6to4 过渡地址，内嵌 IPv4（RFC 3056）"),
    ("fc00::/7",        "唯一本地地址 ULA（RFC 4193）"),
    ("fe80::/10",       "链路本地地址（RFC 4291）"),
    ("ff00::/8",        "组播地址（RFC 4291）"),
]

_V4_NETS = [(ipaddress.IPv4Network(c), why) for c, why in BLOCKED_V4]
_V6_NETS = [(ipaddress.IPv6Network(c), why) for c, why in BLOCKED_V6]

DEFAULT_ALLOWED_SCHEMES = ("http", "https")
DEFAULT_ALLOWED_PORTS = (80, 443)
DEFAULT_DNS_TIMEOUT = 3.0
MAX_REDIRECTS = 5
MAX_BODY_BYTES = 1 << 20  # 1 MiB

# inet_aton 可识别的 IPv4 特殊写法字符集（十进制整数 / 十六进制 / 八进制 / 省略段）
_NUMERIC_V4_RE = re.compile(r"^[0-9a-fA-FxX.]+$")
# netloc 中不应出现的字符（防 URL 解析器差异导致的绕过，如反斜杠）
_BAD_NETLOC_RE = re.compile(r"[\\/\s\x00-\x1f\x7f#?]")


class ResolutionTimeout(Exception):
    """DNS 解析超时。"""


class ValidationResult(NamedTuple):
    ok: bool
    reason: str
    url: str
    host: Optional[str] = None
    ips: tuple = ()
    elapsed_ms: float = 0.0


class FetchResult(NamedTuple):
    ok: bool
    reason: str
    url: str
    status: int = 0
    headers: dict = {}
    body: bytes = b""
    hops: int = 0
    validations: tuple = ()


# ---------------------------------------------------------------------------
# IP 判定
# ---------------------------------------------------------------------------

def parse_ip_literal(host: str) -> Optional[ipaddress._BaseAddress]:
    """把主机串解析为 IP 字面量；不是 IP 字面量时返回 None。

    覆盖：标准点分十进制、IPv6（含 ::ffff:a.b.c.d 映射写法），
    以及 inet_aton 语义的特殊数字写法（十进制整数、十六进制、八进制、
    省略段如 127.1）。无法识别时返回 None，交由 DNS 解析兜底——
    最终判定永远落在「解析得到的 IP」上，而非字符串形式上。
    """
    text = host.strip().rstrip(".")
    if not text:
        return None
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        pass
    if _NUMERIC_V4_RE.match(text):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(text))
        except (OSError, ValueError):
            return None
    return None


def blocked_reason(ip: ipaddress._BaseAddress) -> Optional[str]:
    """返回 IP 被封禁的原因；不在封禁段内返回 None。"""
    if isinstance(ip, ipaddress.IPv4Address):
        for net, why in _V4_NETS:
            if ip in net:
                return f"{ip} 命中封禁段 {net}（{why}）"
        return None
    mapped = ip.ipv4_mapped
    if mapped is not None:
        inner = blocked_reason(mapped)
        if inner:
            return f"{ip} 为 IPv4 映射地址，内嵌 {inner}"
    for net, why in _V6_NETS:
        if ip in net:
            return f"{ip} 命中封禁段 {net}（{why}）"
    return None


# ---------------------------------------------------------------------------
# DNS 解析（可注入 mock；带超时，超时即拒绝——fail closed）
# ---------------------------------------------------------------------------

_RESOLVER_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ssrf-dns")

Resolver = Callable[[str, int], Sequence[str]]


def system_resolver(host: str, port: int) -> Sequence[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def resolve_host(host: str, port: int, timeout: float,
                 resolver: Optional[Resolver]) -> list:
    """解析主机名，返回 IP 字符串列表。超时或失败均抛异常（调用方按拒绝处理）。"""
    fn = resolver or system_resolver
    future = _RESOLVER_POOL.submit(fn, host, port)
    try:
        ips = list(future.result(timeout=timeout))
    except FutureTimeout:
        raise ResolutionTimeout(f"DNS 解析超时（>{timeout}s）: {host}")
    if not ips:
        raise socket.gaierror(f"无解析结果: {host}")
    return ips


# ---------------------------------------------------------------------------
# URL 校验
# ---------------------------------------------------------------------------

def validate_url(url: str,
                 resolver: Optional[Resolver] = None,
                 timeout: float = DEFAULT_DNS_TIMEOUT,
                 allowed_schemes: Sequence[str] = DEFAULT_ALLOWED_SCHEMES,
                 allowed_ports: Sequence[int] = DEFAULT_ALLOWED_PORTS,
                 allow_userinfo: bool = False) -> ValidationResult:
    """校验外呼 URL。任何一步不通过即拒绝（fail closed）。"""
    started = time.perf_counter()

    def reject(reason: str, host: Optional[str] = None) -> ValidationResult:
        return ValidationResult(False, reason, url, host, (),
                                (time.perf_counter() - started) * 1000)

    if not isinstance(url, str) or len(url) > 2048:
        return reject("URL 非法或超长")

    try:
        parts = urlsplit(url)
    except ValueError as exc:
        return reject(f"URL 解析失败: {exc}")

    scheme = parts.scheme.lower()
    if scheme not in allowed_schemes:
        return reject(f"协议不允许: {scheme or '(缺失)'}（仅允许 {tuple(allowed_schemes)}）")

    if _BAD_NETLOC_RE.search(parts.netloc):
        return reject("netloc 含非法字符（反斜杠/空白/控制符），疑似解析器差异绕过")

    if not allow_userinfo and (parts.username is not None or parts.password is not None):
        return reject("URL 含 userinfo（user@host），存在用户名欺骗风险，默认拒绝")

    host = parts.hostname
    if not host:
        return reject("缺少主机名")
    host = host.lower()

    try:
        port = parts.port
    except ValueError:
        return reject("端口非法")
    if port is None:
        port = 443 if scheme == "https" else 80
    if port not in allowed_ports:
        return reject(f"端口不允许: {port}（仅允许 {tuple(allowed_ports)}）")

    # 1) 主机本身是 IP 字面量（含特殊数字写法）——直接判定
    literal = parse_ip_literal(host)
    if literal is not None:
        why = blocked_reason(literal)
        if why:
            return reject(why, host)
        return ValidationResult(True, "ok", url, host, (str(literal),),
                                (time.perf_counter() - started) * 1000)

    # 2) 域名——解析后对所有 A/AAAA 记录逐一判定（防「域名解析到内网」）
    try:
        ips = resolve_host(host, port, timeout, resolver)
    except ResolutionTimeout as exc:
        return reject(str(exc), host)
    except (socket.gaierror, OSError, UnicodeError, ValueError) as exc:
        return reject(f"DNS 解析失败: {exc}", host)

    for text in ips:
        try:
            ip = ipaddress.ip_address(text)
        except ValueError:
            return reject(f"解析结果非法: {text!r}", host)
        why = blocked_reason(ip)
        if why:
            return reject(f"域名 {host} 解析到被封禁地址: {why}", host)

    return ValidationResult(True, "ok", url, host, tuple(ips),
                            (time.perf_counter() - started) * 1000)


# ---------------------------------------------------------------------------
# 安全外呼：校验 + IP 钉扎连接 + 逐跳重定向校验
# ---------------------------------------------------------------------------

Transport = Callable[[str, str, str, float], tuple]  # (method, url, pinned_ip, timeout) -> (status, headers, body)


def pinned_transport(method: str, url: str, pinned_ip: str, timeout: float) -> tuple:
    """默认传输层：连接到「校验时解析出的 IP」，而非重新解析域名。

    这是防 DNS 重绑定（rebinding）的关键：校验与连接使用同一次解析结果。
    HTTPS 下 SNI 与证书校验仍使用原始域名，保证 TLS 安全语义不变。
    """
    parts = urlsplit(url)
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)

    raw = socket.create_connection((pinned_ip, port), timeout=timeout)
    if parts.scheme == "https":
        context = ssl.create_default_context()
        sock = context.wrap_socket(raw, server_hostname=host)
    else:
        sock = raw

    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    conn.sock = sock  # 预置已钉扎的连接，跳过 connect() 内的再次解析
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    host_header = host if parts.port is None else f"{host}:{parts.port}"
    conn.request(method, path, headers={
        "Host": host_header,
        "User-Agent": "ssrf-guard/1.0",
        "Connection": "close",
    })
    resp = conn.getresponse()
    body = resp.read(MAX_BODY_BYTES + 1)[:MAX_BODY_BYTES]
    headers = {k.lower(): v for k, v in resp.getheaders()}
    conn.close()
    return resp.status, headers, body


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def safe_fetch(url: str,
               resolver: Optional[Resolver] = None,
               timeout: float = DEFAULT_DNS_TIMEOUT,
               max_redirects: int = MAX_REDIRECTS,
               transport: Optional[Transport] = None,
               **validate_kwargs) -> FetchResult:
    """安全外呼：每一跳（含重定向后）都重新校验，且连接钉扎到已校验 IP。"""
    send = transport or pinned_transport
    validations = []
    current = url

    for hop in range(max_redirects + 1):
        result = validate_url(current, resolver=resolver, timeout=timeout,
                              **validate_kwargs)
        validations.append(result)
        if not result.ok:
            return FetchResult(False, f"第 {hop + 1} 跳被拒绝: {result.reason}",
                               current, hops=hop, validations=tuple(validations))
        pinned_ip = result.ips[0]
        try:
            status, headers, body = send("GET", current, pinned_ip, timeout)
        except (OSError, http.client.HTTPException) as exc:
            return FetchResult(False, f"连接失败: {exc}", current, hops=hop,
                               validations=tuple(validations))
        if status in _REDIRECT_STATUSES and headers.get("location"):
            current = urljoin(current, headers["location"])
            continue
        return FetchResult(True, "ok", current, status, headers, body,
                           hops=hop, validations=tuple(validations))

    return FetchResult(False, f"重定向次数超过上限（{max_redirects}）",
                       current, hops=max_redirects,
                       validations=tuple(validations))
