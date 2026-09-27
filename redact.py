"""字段级审计日志脱敏库（仅标准库）。

能力：
- 按字段路径（字段名规则）与内容特征（正则 + 校验位）识别敏感值；
- 脱敏结果保留 sha256 前 12 位摘要，可校验、不可还原明文；
- 递归覆盖嵌套 dict / list 的任意深度；
- 字段名被改写时由内容规则兜底；自由文本中的敏感值按匹配段替换；
- 超长字符串截断并保留整体摘要；
- 每次过滤动作写入留痕（路径 / 规则 / 动作 / 摘要 / 原长度）。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

DIGEST_LEN = 12
MAX_STRING_LEN = 4096  # 超过即视为超长日志行，截断留摘要
TRUNC_HEAD = 512


def digest12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:DIGEST_LEN]


def _normalize(name: str) -> str:
    return re.sub(r"[_\-\s]", "", name).lower()


# ---------------------------------------------------------------- 字段名规则
# 命中字段名（路径末段，忽略大小写与 _/-）即整体脱敏，不保留明文。
FIELD_NAME_RULES: Dict[str, str] = {
    "password": "field.password",
    "passwd": "field.password",
    "pwd": "field.password",
    "secret": "field.secret",
    "secretkey": "field.secret",
    "token": "field.token",
    "accesstoken": "field.token",
    "refreshtoken": "field.token",
    "idtoken": "field.token",
    "apikey": "field.apikey",
    "authorization": "field.auth_header",
    "auth": "field.auth_header",
    "cookie": "field.cookie",
    "setcookie": "field.cookie",
    "sessionid": "field.session",
    "creditcard": "field.bankcard",
    "bankcard": "field.bankcard",
    "cardnumber": "field.bankcard",
    "idcard": "field.idcard",
    "idnumber": "field.idcard",
    "ssn": "field.ssn",
    "mobile": "field.phone",
    "phone": "field.phone",
    "phonenumber": "field.phone",
    "email": "field.email",
    "privatekey": "field.privatekey",
}


# ---------------------------------------------------------------- 内容特征规则
def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _idcard_ok(s: str) -> bool:
    if len(s) != 18:
        return False
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    codes = "10X98765432"
    try:
        total = sum(int(s[i]) * weights[i] for i in range(17))
    except ValueError:
        return False
    return codes[total % 11] == s[17].upper()


# (rule_id, pattern, 可选校验函数)。校验函数返回 False 时保留原文（视为误报）。
CONTENT_RULES: List[Tuple[str, "re.Pattern[str]", Optional[Callable[[str], bool]]]] = [
    ("content.pem_private_key",
     re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S),
     None),
    ("content.jwt",
     re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"),
     None),
    ("content.bearer",
     re.compile(r"Bearer\s+[A-Za-z0-9._~+/=\-]{8,}", re.I),
     None),
    ("content.aws_akid",
     re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])"),
     None),
    ("content.kv_secret",
     re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)"
                r"\s*[:=]\s*[\"']?[^\s\"',;&]{4,}"),
     None),
    ("content.email",
     re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
     None),
    ("content.idcard",
     re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
     _idcard_ok),
    ("content.phone_cn",
     re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
     None),
    ("content.bankcard",
     re.compile(r"(?<!\d)\d{13,19}(?!\d)"),
     _luhn_ok),
]


# ---------------------------------------------------------------- 留痕
class Trail:
    """过滤动作留痕：谁被处理、命中哪条规则、留下什么摘要。"""

    def __init__(self) -> None:
        self.entries: List[Dict[str, Any]] = []

    def record(self, path: str, rule: str, action: str, digest: str, orig_len: int) -> None:
        self.entries.append({
            "path": path,
            "rule": rule,
            "action": action,
            "sha256_12": digest,
            "orig_len": orig_len,
        })

    def as_list(self) -> List[Dict[str, Any]]:
        return list(self.entries)


# ---------------------------------------------------------------- 核心过滤
def _mask(rule: str, original: str) -> str:
    return "[REDACTED#{}#{}]".format(rule, digest12(original))


def _scrub_text(text: str, path: str, trail: Trail) -> str:
    out = text
    for rule_id, pattern, validator in CONTENT_RULES:
        def repl(m: "re.Match[str]", _rule=rule_id, _val=validator) -> str:
            matched = m.group(0)
            if _val is not None and not _val(matched):
                return matched
            trail.record(path, _rule, "mask_text", digest12(matched), len(matched))
            return _mask(_rule, matched)
        if rule_id == "content.kv_secret":
            # kv 形式只脱敏值部分，保留键名便于审计阅读
            def kv_repl(m: "re.Match[str]", _rule=rule_id) -> str:
                whole = m.group(0)
                key_part = re.split(r"[:=]", whole, maxsplit=1)[0]
                sep_and_val = whole[len(key_part):]
                sep = sep_and_val[:1]
                val = sep_and_val[1:].lstrip().strip("\"'")
                trail.record(path, _rule, "mask_text", digest12(val), len(val))
                return "{}{}{}".format(key_part, sep, _mask(_rule, val))
            out = pattern.sub(kv_repl, out)
        else:
            out = pattern.sub(repl, out)
    if len(out) > MAX_STRING_LEN:
        trail.record(path, "content.oversize", "truncate", digest12(out), len(out))
        out = "{}...[TRUNCATED#oversize#{}#orig_len={}]".format(out[:TRUNC_HEAD], digest12(out), len(out))
    return out


def sanitize(obj: Any, trail: Optional[Trail] = None, path: str = "$") -> Any:
    """递归脱敏。返回新对象，不修改入参；留痕写入 trail。"""
    if trail is None:
        trail = Trail()
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            child_path = "{}.{}".format(path, key)
            rule = FIELD_NAME_RULES.get(_normalize(str(key)))
            if rule is not None:
                canonical = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
                trail.record(child_path, rule, "redact_field", digest12(canonical), len(canonical))
                result[key] = _mask(rule, canonical)
            else:
                result[key] = sanitize(value, trail, child_path)
        return result
    if isinstance(obj, (list, tuple)):
        return [sanitize(item, trail, "{}[{}]".format(path, i)) for i, item in enumerate(obj)]
    if isinstance(obj, str):
        return _scrub_text(obj, path, trail)
    return obj


def filter_line(line: str, trail: Optional[Trail] = None, path: str = "$line") -> str:
    """处理非 JSON 的原始日志行：内容规则 + 超长截断。"""
    if trail is None:
        trail = Trail()
    return _scrub_text(line, path, trail)
