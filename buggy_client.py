"""原始（有缺陷）的重定向跟随实现 —— 仅用于复现问题。

缺陷：只用一个跳数计数器兜底，没有任何"已访问地址"记录。
两个地址互跳时，客户端会在它们之间无限来回（生产上表现为
偶发卡死、直到 socket 超时），完全依赖 max_hops 才被截断。
"""

from __future__ import annotations

import http.client
import urllib.parse

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
DEFAULT_PORTS = {"http": 80, "https": 443}


class TooManyRedirectsError(Exception):
    def __init__(self, max_hops):
        super().__init__("exceeded max_hops=%d" % max_hops)
        self.max_hops = max_hops


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


def follow_redirects(url, max_hops=20, fetch=None):
    """缺陷版：只有跳数上限，没有环路检测。"""
    fetch = fetch or default_fetch
    current = url
    hops = 0
    while True:
        status, headers, _body = fetch(current)
        if status not in REDIRECT_STATUSES:
            return status, headers
        location = headers.get("location")
        if not location:
            return status, headers
        current = urllib.parse.urljoin(current, location)
        hops += 1
        if hops >= max_hops:  # 唯一的兜底：互跳时白白打满所有跳数
            raise TooManyRedirectsError(max_hops)
