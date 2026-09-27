"""对拍：negotiation.py（被测） vs reference.py（参照）。

随机生成「客户端偏好列表 × 服务端能力列表」组合，两边选择结果必须一致；
不一致时按元素删除做 1-最小收缩，输出最小反例。

用法：python3 difftest.py [用例数] [随机种子]
"""
import random
import sys

import negotiation
import reference

TYPES = ["text", "application", "image", "*"]
SUBTYPES = ["html", "plain", "json", "xml", "*"]
PARAMS = [("level", "1"), ("level", "2"), ("charset", "utf-8"), ("version", "1")]
QS = [None, "1", "0", "0.5", "0.9", "0.300", "1.0", "0.000", "0.07"]


def gen_range(rng):
    t = rng.choice(TYPES)
    st = "*" if t == "*" else rng.choice(SUBTYPES)
    s = "%s/%s" % (t, st)
    for k, v in rng.sample(PARAMS, rng.randint(0, 2)):
        s += ";%s=%s" % (k, v)
    q = rng.choice(QS)
    if q is not None:
        s += ";q=%s" % q
    return s


def gen_variant(rng):
    t = rng.choice(TYPES[:-1])
    st = rng.choice(SUBTYPES[:-1])
    s = "%s/%s" % (t, st)
    for k, v in rng.sample(PARAMS, rng.randint(0, 2)):
        s += ";%s=%s" % (k, v)
    return s


def gen_case(rng):
    r = rng.random()
    if r < 0.08:
        header = None  # 客户端未提供偏好
    else:
        header = [gen_range(rng) for _ in range(rng.randint(0, 5))]
    variants = [gen_variant(rng) for _ in range(rng.randint(0, 5))]
    return {"header": header, "variants": variants}


def run_both(case):
    h = None if case["header"] is None else ",".join(case["header"])
    return negotiation.select(h, case["variants"]), reference.select(h, case["variants"])


def mismatch(case):
    a, b = run_both(case)
    return a != b


def minimize(case):
    """在保持不一致的前提下反复删除元素，直到 1-最小（删任一元素即一致）。"""
    case = {
        "header": None if case["header"] is None else list(case["header"]),
        "variants": list(case["variants"]),
    }
    improved = True
    while improved:
        improved = False
        for key in ("header", "variants"):
            lst = case[key]
            if lst is None:
                continue
            i = 0
            while i < len(lst):
                trial = lst[:i] + lst[i + 1:]
                cand = {**case, key: trial}
                if mismatch(cand):
                    case = cand
                    lst = trial
                    improved = True
                else:
                    i += 1
    return case


FIXED_CASES = [
    {"header": ["*/*"], "variants": ["text/html", "application/json"]},
    {"header": ["*/*;q=0", "text/html;q=0"], "variants": ["text/html"]},
    {"header": None, "variants": ["text/html", "text/plain"]},
    {"header": ["text/html"], "variants": []},
    {"header": [], "variants": []},
]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260928
    rng = random.Random(seed)

    cases = list(FIXED_CASES) + [gen_case(rng) for _ in range(n)]
    for i, case in enumerate(cases):
        if mismatch(case):
            small = minimize(case)
            h = None if small["header"] is None else ",".join(small["header"])
            a, b = run_both(small)
            print("发现不一致！最小反例（1-最小，删任一元素即恢复一致）：")
            print("  Accept:      %r" % (h,))
            print("  服务端能力:  %r" % (small["variants"],))
            print("  negotiation -> %r" % (a,))
            print("  reference   -> %r" % (b,))
            return 1
    print("%d 个用例（含 %d 个固定边界用例）全部一致，seed=%d。"
          % (len(cases), len(FIXED_CASES), seed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
