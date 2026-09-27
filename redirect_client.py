"""修复版重定向跟随客户端（仅标准库）。

两层保护：
1. 环路检测（主）：对每次要请求的地址做规范化后查重，
   命中即抛 RedirectLoopError，并附带完整跳转链。
2. 跳数上限（兜底）：max_hops 只用于拦截"地址永不重复但
   无限变长"的链（如 /redirect?n=1 -> n=2 -> ...），
   此时环路检测永远帮不上忙，必须靠 TooManyRedirectsError 截断。
"""

from __future__ import annotations

import http.client
import urllib.parse
from collections import namedtuple

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
DEFAULT_PORTS = {"http": 80, "https": 443}
DEFAULT_MAX_HOPS = 20

Response = namedtuple("Response", ["status", "headers", "body", "chain"])


class RedirectError(Exception):
    """重定向处理失败的基类，均携带完整跳转链 chain。"""

    def __init__(self, message, chain):
        self.chain = list(chain)
        detail = "\n".join("  %d. %s" % (i + 1, u) for i, u in enumerate(self.chain))
        super().__init__("%s\nredirect chain (%d hops):\n%s" % (message, len(self.chain), detail))


class RedirectLoopError(RedirectError):
    """检出重复访问（规范化后等价）的地址。"""

    def __init__(self, chain, repeated_url, first_visit_index):
        self.repeated_url = repeated_url
        self.first_visit_index = first_visit_index
        msg = "redirect loop detected: %s (first visited at hop %d)" % (
            repeated_url,
            first_visit_index + 1,
        )
        super().__init__(msg, chain)


class TooManyRedirectsError(RedirectError):
    """跳数上限兜底：链中地址互不重复但超过了 max_hops。"""

    def __init__(self, chain, max_hops):
        self.max_hops = max_hops
        super().__init__("too many redirects (max_hops=%d)" % max_hops, chain)


def _remove_dot_segments(path):
    out = []
    for seg in path.split("/"):
        if seg == ".":
            continue
        if seg == "..":
            if out and out[-1] != "":
                out.pop()
            continue
        out.append(seg)
    result = "/".join(out)
    if path.startswith("/") and not result.startswith("/"):
        result = "/" + result
    return result or "/"


def normalize_url(url):
    """把等价写法归一到同一键，用于精确查重。

    覆盖：scheme/host 大小写、默认端口、空路径、点段、
    百分号编码（解码后再按统一规则编码）、查询参数顺序、fragment。
    注意：查询参数"值"不同仍视为不同地址；仅顺序/空参数差异视为等价。
    """
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or "http").lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is None or port == DEFAULT_PORTS.get(scheme):
        netloc = host
    else:
        netloc = "%s:%d" % (host, port)

    path = urllib.parse.unquote(parts.path or "/")
    path = _remove_dot_segments(path)
    path = urllib.parse.quote(path, safe="/-._~!$&'()*+,;=:@%")

    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = urllib.parse.urlencode(sorted(pairs))

    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def default_fetch(url, timeout=10):
    parts = urllib.parse.urlsplit(url)
    conn_cls = (
        http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    )
    conn = conn_cls(parts.hostname, parts.port or DEFAULT_PORTS.get(parts.scheme), timeout=timeout)
    path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    headers = {k.lower(): v for k, v in resp.getheaders()}
    conn.close()
    return resp.status, headers, body


def follow_redirects(url, max_hops=DEFAULT_MAX_HOPS, fetch=None):
    """跟随重定向直到拿到非 3xx 响应。

    返回 Response(status, headers, body, chain)，chain 为完整跳转链。
    检出环路抛 RedirectLoopError；跳数超限抛 TooManyRedirectsError。
    """
    fetch = fetch or default_fetch
    chain = []       # 实际请求过的地址（原始写法，便于阅读）
    visited = {}     # 规范化地址 -> 首次出现在 chain 中的下标
    current = url

    while True:
        key = normalize_url(current)
        if key in visited:
            chain.append(current)
            raise RedirectLoopError(chain, current, visited[key])
        visited[key] = len(chain)
        chain.append(current)

        status, headers, body = fetch(current)
        if status not in REDIRECT_STATUSES:
            return Response(status, headers, body, list(chain))
        location = headers.get("location")
        if not location:
            return Response(status, headers, body, list(chain))

        if len(chain) > max_hops:  # 兜底：链无限变长但永不重复时截断
            raise TooManyRedirectsError(chain, max_hops)
        current = urllib.parse.urljoin(current, location)
