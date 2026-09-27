"""篡改检出演示：python3 demo.py"""

from urllib.parse import parse_qsl, urlsplit

import signed_url as su

KEY = b"demo-secret"
NOW = 1_700_000_000


def clock():
    return NOW


def tamper(url, transform):
    split = urlsplit(url)
    pairs = transform(parse_qsl(split.query, keep_blank_values=True))
    return f"{split.path}?" + "&".join(f"{n}={v}" for n, v in pairs)


def show(title, url, original_params):
    result = su.verify(url, KEY, clock=clock)
    print(f"[{title}]")
    print(f"  ok={result.ok} reason={result.reason} detail={result.detail}")
    if not result.ok and result.params:
        diff = result.diff_against(original_params)
        if diff:
            print(f"  差异: added={diff.added} removed={diff.removed} "
                  f"changed={diff.changed_names}")
    print()


params = [("uid", "328"), ("uid", "999"), ("empty", ""), ("mode", "fast")]
url = su.build_url("/download/report.pdf", params, KEY,
                   not_before=NOW - 10, expires_at=NOW + 600)
print(f"签发链接:\n  {url}\n")

show("原始链接（应通过）", url, params)
show("新增参数 admin=1", tamper(url, lambda ps: [("admin", "1")] + ps), params)
show("删除参数 mode=fast",
     tamper(url, lambda ps: [p for p in ps if p != ("mode", "fast")]), params)
show("修改 uid 328->329",
     tamper(url, lambda ps: [("uid", "329") if p == ("uid", "328") else p
                             for p in ps]), params)
show("篡改过期时间 _exp",
     tamper(url, lambda ps: [("_exp", "9999999999") if p[0] == "_exp" else p
                             for p in ps]), params)
show("篡改资源路径", url.replace("report.pdf", "admin.pdf"), params)
show("篡改签名末位", url[:-1] + ("0" if url[-1] != "0" else "1"), params)
show("仅调整参数顺序（合法，应通过）",
     tamper(url, lambda ps: list(reversed(ps))), params)
