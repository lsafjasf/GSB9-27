"""HTTP 重定向跟随客户端（仅标准库）。

两层防护：
1. 环路检测（精确）：对每次跳转目标做 URL 规范化，命中已访问集合即判定成环，
   抛出 RedirectLoopError 并携带完整跳转链。
2. 跳数上限（兜底）：max_redirects 限制总跳数，防止目标永不重复但无限延伸的
   重定向链（例如每次追加递增参数的“计数器”型跳转），此时环路检测无法命中。
"""
from __future__ import annotations

import http.client
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
DEFAULT_MAX_REDIRECTS = 10

_UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
_PCT_RE = re.compile(r"%([0-9A-Fa-f]{2})")


class RedirectError(Exception):
    """重定向相关错误基类。"""


class RedirectLoopError(RedirectError):
    """检测到重复访问（规范化后等价）的地址，chain 为完整跳转链。"""

    def __init__(self, chain):
        self.chain = list(chain)
        pretty = "\n  -> ".join(self.chain)
        super().__init__(
            "redirect loop detected after %d hop(s):\n  %s" % (len(self.chain) - 1, pretty)
        )


class TooManyRedirectsError(RedirectError):
    """超过跳数上限（兜底保护，链未必成环）。"""

    def __init__(self, chain, max_redirects):
        self.chain = list(chain)
        self.max_redirects = max_redirects
        super().__init__(
            "exceeded max_redirects=%d; last chain:\n  %s"
            % (max_redirects, "\n  -> ".join(self.chain))
        )


def _remove_dot_segments(path):
    """RFC 3986 5.2.4 点段消除。"""
    out = []
    for seg in path.split("/"):
        if seg == ".":
            continue
        if seg == "..":
            if out:
                out.pop()
            continue
        out.append(seg)
    result = "/".join(out)
    if not result.startswith("/"):
        result = "/" + result
    return result


def _decode_unreserved(text):
    def repl(match):
        ch = chr(int(match.group(1), 16))
        return ch if ch in _UNRESERVED else match.group(0).upper()

    return _PCT_RE.sub(repl, text)


def normalize_url(url):
    """规范化 URL，使等价写法得到相同结果：

    - scheme/host 小写化，host 末尾根点去掉
    - 默认端口（http:80 / https:443）省略
    - 空路径视为 "/"，消除 "." / ".." 点段
    - 路径中非保留字符的 %XX 解码
    - 查询参数按键值对排序（顺序不同视为同一资源），空查询与 "?" 等价
    - fragment 丢弃（不参与 HTTP 请求）
    """
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "http").lower()
    host = (parts.hostname or "").lower().rstrip(".")
    try:
        port = parts.port
    except ValueError:
        port = None
    if port == DEFAULT_PORTS.get(scheme):
        port = None
    netloc = host if port is None else "%s:%d" % (host, port)
    path = _decode_unreserved(_remove_dot_segments(parts.path or "/"))
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    query = urlencode(sorted(pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


def default_fetch(url):
    """真实网络请求：GET 一次，不自动跟随重定向。"""
    parts = urlsplit(url)
    scheme = (parts.scheme or "http").lower()
    conn_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    port = parts.port or DEFAULT_PORTS[scheme]
    conn = conn_cls(parts.hostname, port, timeout=10)
    target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    conn.request("GET", target, headers={"Host": parts.netloc, "Connection": "close"})
    resp = conn.getresponse()
    body = resp.read()
    headers = {k.lower(): v for k, v in resp.getheaders()}
    status = resp.status
    conn.close()
    return status, headers, body


def follow_redirects(url, max_redirects=DEFAULT_MAX_REDIRECTS, fetch=default_fetch):
    """跟随重定向直到非 3xx 响应。

    返回 (status, headers, body, chain)；chain 为完整跳转链（含起始 URL）。
    检出环路抛 RedirectLoopError；超过 max_redirects 抛 TooManyRedirectsError。
    """
    chain = [url]
    seen = {normalize_url(url)}
    current = url
    while True:
        status, headers, body = fetch(current)
        location = headers.get("location")
        if status not in REDIRECT_STATUSES or not location:
            return status, headers, body, chain
        nxt = urljoin(current, location)  # 支持相对地址
        norm = normalize_url(nxt)
        if norm in seen:
            raise RedirectLoopError(chain + [nxt])
        if len(chain) > max_redirects:
            raise TooManyRedirectsError(chain + [nxt], max_redirects)
        seen.add(norm)
        chain.append(nxt)
        current = nxt
