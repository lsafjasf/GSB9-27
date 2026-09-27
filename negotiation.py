"""HTTP 内容协商（Accept）打分与选择库。仅使用标准库。

规则详见 RULES.md，要点：
- 每条服务端表示的生效权重 = 匹配它的「最具体」客户端范围的 q 值
  （具体优先于通配，但显式写出的 q 永远生效，即使比通配的 q 低）。
- 选择顺序：生效 q 高者优先；并列时依次比较匹配范围的具体度、
  参数个数、在客户端列表中的位置、在服务端列表中的位置。
- q=0 表示显式拒绝；客户端未提供偏好时取服务端第一项；
  服务端能力为空时返回 None。
"""


def parse_q(raw):
    """把 q 值解析为千分制整数 [0, 1000]；非法返回 None。

    合法形式：0 / 1 / 0.x / 0.xx / 0.xxx / 1.0 / 1.00 / 1.000。
    """
    s = raw.strip()
    if not s:
        return None
    if s[0] == "1":
        rest = s[1:]
        if rest == "":
            return 1000
        if rest.startswith(".") and 1 <= len(rest) - 1 <= 3 and set(rest[1:]) == {"0"}:
            return 1000
        return None
    if s[0] == "0":
        rest = s[1:]
        if rest == "":
            return 0
        if rest.startswith(".") and 1 <= len(rest) - 1 <= 3 and rest[1:].isdigit():
            return int(rest[1:].ljust(3, "0"))
        return None
    return None


def _split_params(item):
    """把 'a/b ; k=v ; k2=v2' 拆成 (type_part, [(k, v), ...])。

    无 '=' 的参数段记为 (k, None)，由调用方判为非法。
    """
    parts = item.split(";")
    type_part = parts[0].strip().lower()
    pairs = []
    for p in parts[1:]:
        if "=" not in p:
            pairs.append((p.strip().lower(), None))
            continue
        k, v = p.split("=", 1)
        pairs.append((k.strip().lower(), v.strip()))
    return type_part, pairs


def parse_accept(header):
    """解析 Accept 头，返回范围列表 [(type, subtype, params, q_milli, pos)]。

    header 为 None 或空串时返回 []。非法条目（无 '/'、参数无 '='、
    q 值非法）整条丢弃，不影响其余条目；pos 为在原始列表中的序号。
    类型/子类型与参数名小写化；参数值保持原样（区分大小写）。
    """
    if not header:
        return []
    ranges = []
    for pos, item in enumerate(header.split(",")):
        item = item.strip()
        if not item:
            continue
        type_part, pairs = _split_params(item)
        if "/" not in type_part:
            continue
        t, st = (s.strip() for s in type_part.split("/", 1))
        if not t or not st:
            continue
        params = {}
        q = 1000
        valid = True
        for k, v in pairs:
            if v is None:
                valid = False
                break
            if k == "q":
                q = parse_q(v)
                if q is None:
                    valid = False
                    break
            else:
                params[k] = v  # 同名参数后者覆盖前者
        if valid:
            ranges.append((t, st, params, q, pos))
    return ranges


def parse_variant(item):
    """解析服务端能力条目，返回 (type, subtype, params)；非法返回 None。

    服务端条目中的 q 无特殊含义，按普通参数处理。
    """
    type_part, pairs = _split_params(item.strip())
    if "/" not in type_part:
        return None
    t, st = (s.strip() for s in type_part.split("/", 1))
    if not t or not st:
        return None
    params = {}
    for k, v in pairs:
        if v is None:
            return None
        params[k] = v
    return t, st, params


def _match(rng, variant):
    """rng 匹配 variant 时返回 (具体度, 参数个数)，否则返回 None。

    具体度：精确类型=2，type/*=1，*/*=0。'*/html' 这类写法非法，不匹配。
    范围上声明的参数必须全部出现在变体中且取值相等。
    """
    rt, rst, rparams, _, _ = rng
    vt, vst, vparams = variant
    if rt == "*" and rst != "*":
        return None
    if rt != "*" and rt != vt:
        return None
    if rst != "*" and rst != vst:
        return None
    for k, v in rparams.items():
        if vparams.get(k) != v:
            return None
    spec = 0 if rt == "*" else (1 if rst == "*" else 2)
    return spec, len(rparams)


def select(accept_header, server_variants):
    """在 server_variants 中选出最符合 accept_header 的表示。

    返回服务端列表中的原始字符串；无法协商时返回 None。
    判定顺序见模块 docstring 与 RULES.md。
    """
    parsed = []
    for idx, raw in enumerate(server_variants):
        v = parse_variant(raw)
        if v is not None:
            parsed.append((idx, raw, v))
    if not parsed:
        return None

    ranges = parse_accept(accept_header)
    if not ranges:
        # 客户端未提供偏好：等价于 */*，按服务端顺序取第一项
        return parsed[0][1]

    best_key = None
    best_raw = None
    for idx, raw, variant in parsed:
        # 该变体的生效范围：具体度高者优先，其次参数多者，其次客户端列表中靠前者
        chosen = None
        for rng in ranges:
            m = _match(rng, variant)
            if m is None:
                continue
            key = (m[0], m[1], -rng[4])
            if chosen is None or key > chosen[0]:
                chosen = (key, rng[3])
        if chosen is None:
            continue
        (spec, nparams, negpos), q = chosen
        if q == 0:
            continue  # 显式拒绝
        key = (q, spec, nparams, negpos, -idx)
        if best_key is None or key > best_key:
            best_key = key
            best_raw = raw
    return best_raw
