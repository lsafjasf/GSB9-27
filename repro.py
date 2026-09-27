"""复现脚本：对照旧实现（仅跳数上限）与修复实现（环路检测 + 上限兜底）。

运行: python3 repro.py
"""
import time

from redirect_client import (
    RedirectLoopError,
    TooManyRedirectsError,
    follow_redirects,
)
from redirect_client import REDIRECT_STATUSES


def buggy_follow(url, max_redirects=10, fetch=None):
    """旧实现：只有跳数上限，没有环路检测 —— 两个地址互跳时一直跳到上限才报错。"""
    chain = [url]
    current = url
    for _ in range(max_redirects + 1):
        status, headers, _body = fetch(current)
        location = headers.get("location")
        if status not in REDIRECT_STATUSES or not location:
            return status, chain
        current = location  # 不解析相对地址、不规范化、不查重
        chain.append(current)
    raise TooManyRedirectsError(chain, max_redirects)


def make_ping_pong_fetch():
    """模拟两台服务器互跳：A <-> B。"""
    table = {
        "http://a.test/start": (302, {"location": "http://b.test/next"}),
        "http://b.test/next": (302, {"location": "http://a.test/start"}),
    }
    calls = []

    def fetch(url):
        calls.append(url)
        return (*table[url], b"")

    return fetch, calls


def main():
    print("=== 旧实现（仅跳数上限兜底）===")
    fetch, calls = make_ping_pong_fetch()
    t0 = time.monotonic()
    try:
        buggy_follow("http://a.test/start", max_redirects=10, fetch=fetch)
    except TooManyRedirectsError as exc:
        dt = time.monotonic() - t0
        print("请求次数: %d（在 A/B 间来回空转，直到撞上限才退出）" % len(calls))
        print("报错: %s" % str(exc).splitlines()[0])

    print()
    print("=== 修复实现（环路检测 + 上限兜底）===")
    fetch, calls = make_ping_pong_fetch()
    try:
        follow_redirects("http://a.test/start", max_redirects=10, fetch=fetch)
    except RedirectLoopError as exc:
        print("请求次数: %d（第 2 跳即检出环路，立即失败）" % len(calls))
        print("报错与完整跳转链:")
        print(exc)


if __name__ == "__main__":
    main()
