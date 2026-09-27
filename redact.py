"""字段级敏感信息脱敏库（仅标准库）。

能力：
- 按字段路径/字段名识别敏感值（含被改写的字段名，如 x_pwd_bak、userPassword2）。
- 按内容特征识别自由文本中的敏感值（口令、JWT、Bearer、邮箱、手机、身份证、银行卡等）。
- 递归覆盖嵌套 dict / list 及数组元素。
- 脱敏结果保留可校验摘要（sha256 前 12 位），不保留明文。
- 全程留痕：每个被处理字段记录路径、命中规则、摘要、原值长度。
- 支持超长日志行：redact_stream 逐行流式处理，内存有界。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, asdict

DIGEST_LEN = 12
PLACEHOLDER_FMT = "[REDACTED:{rule}#{digest}]"

# 敏感词根。字段名先按 下划线/连字符/驼峰/数字 分词，再做词级匹配，
# 避免误伤 author、monkey、tokenize_count 这类含词根但语义无关的字段。
SENSITIVE_KEY_TOKENS = (
    "password", "passwd", "pwd", "passphrase",
    "secret", "token", "apikey", "credential", "credentials",
    "privatekey", "accesskey", "secretkey", "sessionid",
    "authorization", "idcard", "ssn",
)

_WORD_SPLIT = re.compile(r"[^a-zA-Z0-9]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _key_words(key: str):
    """把字段名拆成词：userPassword2 -> [user, password2]；x_pwd_bak -> [x, pwd, bak]。"""
    return [w.lower() for w in _WORD_SPLIT.split(str(key)) if w]


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _match_key_tokens(key: str, tokens):
    words = [re.sub(r"\d+$", "", w) for w in _key_words(key)]
    candidates = set(words)
    candidates.update(words[i] + words[i + 1] for i in range(len(words) - 1))
    candidates.add(_norm_key(key))
    for cand in candidates:
        if cand in tokens:
            return next(t for t in tokens if t == cand)
        # 词的前/后缀含长词根（>=6 字符）也算命中，如 userpassword、secretkey、dbsecret；
        # 短词根不做前后缀匹配，避免 tokenize/author 误报。
        for tok in tokens:
            if len(tok) >= 6 and len(cand) > len(tok) and \
                    (cand.startswith(tok) or cand.endswith(tok)):
                return tok
    return None


def key_rule_id(key: str):
    """字段名命中敏感词根时返回规则 id，否则 None。"""
    tok = _match_key_tokens(key, SENSITIVE_KEY_TOKENS)
    return "field:" + tok if tok else None


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


def _cn_idcard_ok(s: str) -> bool:
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    codes = "10X98765432"
    try:
        return codes[sum(int(s[i]) * weights[i] for i in range(17)) % 11] == s[17].upper()
    except (IndexError, ValueError):
        return False


@dataclass
class ContentRule:
    rule_id: str
    pattern: re.Pattern
    group: int = 1          # 命中的敏感子串所在分组（其余上下文保留）
    validator: object = None  # 可选校验函数，降低误报

    def find(self, text: str):
        for m in self.pattern.finditer(text):
            value = m.group(self.group)
            if self.validator is None or self.validator(value):
                yield m, value


_KV_NAMES = r"(?:password|passwd|pwd|passphrase|secret|token|api[_-]?key|access[_-]?key|secret[_-]?key|authorization|credential)"

CONTENT_RULES = [
    # 自由文本 / 内嵌 JSON 片段中的 key=value、key: value、"key":"value"
    ContentRule("content:kv-secret", re.compile(
        r"(?i)([\"']?" + _KV_NAMES + r"[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;&}{]{4,})"), group=2),
    ContentRule("content:bearer", re.compile(
        r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/=-]{8,})"), group=2),
    ContentRule("content:jwt", re.compile(
        r"\b(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,})\b")),
    ContentRule("content:aws-akid", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    ContentRule("content:email", re.compile(
        r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")),
    ContentRule("content:cn-mobile", re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")),
    ContentRule("content:cn-idcard", re.compile(r"(?<![0-9Xx])(\d{17}[0-9Xx])(?![0-9Xx])"),
                validator=_cn_idcard_ok),
    ContentRule("content:bank-card", re.compile(r"(?<!\d)(\d{16,19})(?!\d)"),
                validator=_luhn_ok),
]


@dataclass
class AuditRecord:
    path: str          # JSON 路径，如 $.user.tokens[2]
    rule: str          # 命中的规则 id
    digest: str        # 原值 sha256 前 12 位（可校验，不可逆）
    value_length: int  # 原值长度
    action: str = "redact"


def digest_of(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:DIGEST_LEN]


class Redactor:
    def __init__(self, extra_key_tokens=(), extra_content_rules=(), audit_limit=None):
        self.key_tokens = SENSITIVE_KEY_TOKENS + tuple(extra_key_tokens)
        self.content_rules = list(CONTENT_RULES) + list(extra_content_rules)
        self.audit: list[AuditRecord] = []
        self.audit_limit = audit_limit  # 流式场景可限制内存中的留痕条数
        self.redaction_count = 0

    # ---- 内部 ----
    def _key_rule_id(self, key: str):
        tok = _match_key_tokens(key, self.key_tokens)
        return "field:" + tok if tok else None

    def _placeholder(self, rule: str, value: str) -> str:
        return PLACEHOLDER_FMT.format(rule=rule, digest=digest_of(value))

    def _record(self, path: str, rule: str, value: str):
        self.redaction_count += 1
        if self.audit_limit is None or len(self.audit) < self.audit_limit:
            self.audit.append(AuditRecord(path, rule, digest_of(value), len(value)))

    # ---- 文本级 ----
    def redact_text(self, text: str, path: str = "$") -> str:
        """对自由文本按内容特征脱敏。对超长行同样适用（正则线性扫描）。"""
        for rule in self.content_rules:
            def _sub(m, _rule=rule):
                value = m.group(_rule.group)
                if _rule.validator is not None and not _rule.validator(value):
                    return m.group(0)
                self._record(path, _rule.rule_id, value)
                s = m.start(_rule.group) - m.start(0)
                e = m.end(_rule.group) - m.start(0)
                whole = m.group(0)
                return whole[:s] + self._placeholder(_rule.rule_id, value) + whole[e:]
            text = rule.pattern.sub(_sub, text)
        return text

    # ---- 结构级 ----
    def redact(self, obj, path: str = "$"):
        """递归脱敏 dict / list / str，返回新对象（不改原对象）。"""
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                child = f"{path}.{k}"
                rule = self._key_rule_id(k)
                if rule is not None:
                    raw = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, sort_keys=True)
                    self._record(child, rule, raw)
                    out[k] = self._placeholder(rule, raw)
                else:
                    out[k] = self.redact(v, child)
            return out
        if isinstance(obj, (list, tuple)):
            return [self.redact(v, f"{path}[{i}]") for i, v in enumerate(obj)]
        if isinstance(obj, str):
            return self.redact_text(obj, path)
        return obj

    def audit_dicts(self):
        return [asdict(r) for r in self.audit]


# ---- 漏网检查 ----
def find_leaks(obj, secrets, path: str = "$"):
    """在脱敏结果中递归搜索明文秘密残留，返回 [(path, secret), ...]，应为空。"""
    leaks = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            leaks.extend(find_leaks(v, secrets, f"{path}.{k}"))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            leaks.extend(find_leaks(v, secrets, f"{path}[{i}]"))
    elif isinstance(obj, str):
        for s in secrets:
            if s and s in obj:
                leaks.append((path, s))
    return leaks


# ---- 流式处理（超长日志行 / 大文件，内存有界）----
def redact_stream(infh, outfh, redactor: Redactor = None, audit_fh=None):
    """逐行处理：JSON 行按结构脱敏，非 JSON 行按文本脱敏。返回统计 dict。"""
    redactor = redactor or Redactor(audit_limit=None)
    stats = {"lines": 0, "json_lines": 0, "text_lines": 0,
             "bytes_in": 0, "bytes_out": 0, "redactions": 0}
    for line in infh:
        stats["lines"] += 1
        stats["bytes_in"] += len(line.encode("utf-8"))
        stripped = line.rstrip("\n")
        obj = None
        if stripped[:1] in "{[":
            try:
                obj = json.loads(stripped)
            except ValueError:
                obj = None
        if obj is not None:
            stats["json_lines"] += 1
            out = json.dumps(redactor.redact(obj), ensure_ascii=False)
        else:
            stats["text_lines"] += 1
            out = redactor.redact_text(stripped, path=f"$line{stats['lines']}")
        stats["bytes_out"] += len(out.encode("utf-8")) + 1
        outfh.write(out + "\n")
    stats["redactions"] = redactor.redaction_count
    if audit_fh is not None:
        for rec in redactor.audit:
            audit_fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
    return stats
