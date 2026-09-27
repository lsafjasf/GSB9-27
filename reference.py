"""内容协商参照实现：RULES.md 规则的独立直译，仅用于对拍。

与 negotiation.py 刻意采用不同的实现路线：
- 正则解析 q 值，用 Fraction 做精确有理数比较（而非千分制整数）；
- 先收集每个变体的全部匹配范围再取最大，最后整体排序选择。
任何与 negotiation.py 的分歧都视为其中一方的实现缺陷。
"""
import re
from fractions import Fraction

_Q_RE = re.compile(r"^([01])(?:\.(\d{1,3}))?$")


def _q(text):
    m = _Q_RE.match(text.strip())
    if not m:
        return None
    whole, frac = m.groups()
    if whole == "1" and frac is not None and set(frac) != {"0"}:
        return None
    if frac is None:
        return Fraction(int(whole))
    return Fraction(int(whole) * 1000 + int(frac.ljust(3, "0")), 1000)


def _entries(header):
    """解析 Accept 头为 dict 列表；非法条目丢弃。"""
    if not header:
        return []
    out = []
    for pos, item in enumerate(header.split(",")):
        item = item.strip()
        if not item:
            continue
        segs = item.split(";")
        media = segs[0].strip().lower()
        if "/" not in media:
            continue
        t, st = (s.strip() for s in media.split("/", 1))
        if not t or not st:
            continue
        params = {}
        q = Fraction(1)
        ok = True
        for seg in segs[1:]:
            if "=" not in seg:
                ok = False
                break
            k, v = seg.split("=", 1)
            k, v = k.strip().lower(), v.strip()
            if k == "q":
                q = _q(v)
                if q is None:
                    ok = False
                    break
            else:
                params[k] = v
        if ok:
            out.append({"type": t, "subtype": st, "params": params,
                        "q": q, "pos": pos})
    return out


def _variant(item):
    segs = item.strip().split(";")
    media = segs[0].strip().lower()
    if "/" not in media:
        return None
    t, st = (s.strip() for s in media.split("/", 1))
    if not t or not st:
        return None
    params = {}
    for seg in segs[1:]:
        if "=" not in seg:
            return None
        k, v = seg.split("=", 1)
        params[k.strip().lower()] = v.strip()
    return {"type": t, "subtype": st, "params": params}


def _specificity(rng, var):
    """匹配则返回 (级别, 参数个数)，否则 None。级别：精确2/类型通配1/全通配0。"""
    if rng["type"] == "*" and rng["subtype"] != "*":
        return None
    if rng["type"] not in ("*", var["type"]):
        return None
    if rng["subtype"] not in ("*", var["subtype"]):
        return None
    for k, v in rng["params"].items():
        if var["params"].get(k) != v:
            return None
    if rng["type"] == "*":
        level = 0
    elif rng["subtype"] == "*":
        level = 1
    else:
        level = 2
    return level, len(rng["params"])


def select(header, variants):
    parsed = []
    for i, raw in enumerate(variants):
        v = _variant(raw)
        if v is not None:
            parsed.append((i, raw, v))
    if not parsed:
        return None

    ranges = _entries(header)
    if not ranges:
        return parsed[0][1]

    table = []
    for i, raw, var in parsed:
        matches = []
        for r in ranges:
            sp = _specificity(r, var)
            if sp is not None:
                matches.append((sp, -r["pos"], r["q"]))
        if not matches:
            continue
        (spec, nparams), negpos, q = max(matches, key=lambda m: (m[0], m[1]))
        if q == 0:
            continue
        table.append(((q, spec, nparams, negpos, -i), raw))
    if not table:
        return None
    table.sort(key=lambda e: e[0])
    return table[-1][1]
