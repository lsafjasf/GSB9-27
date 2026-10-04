"""白名单输入校验库（仅标准库）。

核心原则：
1. 先规范化（normalize），后校验（validate）。所有判断都作用于规范形式，
   攻击者无法通过换编码绕过字符集检查。
2. 白名单：每类输入声明允许的字符集与结构约束，凡不在白名单内一律拒绝，
   而不是枚举非法字符。
"""

import html
import json
import math
import re
import unicodedata
from urllib.parse import unquote_to_bytes

MAX_NORMALIZE_ROUNDS = 3      # 多层编码最多展开 3 层
RAW_INPUT_LIMIT = 4096        # 规范化前的原始长度上限
JSON_MAX_DEPTH = 8
JSON_MAX_NODES = 1000
JSON_MAX_STRING = 256
JSON_MAX_RAW = 16384


class ValidationError(ValueError):
    """输入未通过白名单校验。"""


# ---------------------------------------------------------------------------
# 规范化
# ---------------------------------------------------------------------------

def _decode_once(value):
    """展开一层百分号编码 + 一层 HTML 实体编码，UTF-8 严格解码。"""
    try:
        decoded = unquote_to_bytes(value).decode("utf-8", "strict")
    except (UnicodeDecodeError, ValueError) as exc:
        # 覆盖超长 UTF-8（overlong，如 %C0%AF）、截断序列、孤立 % 等
        raise ValidationError(f"非法编码序列: {exc}") from exc
    return html.unescape(decoded)


def normalize(value, field="input"):
    """把输入收敛为规范形式：多层解码 -> NFKC -> 控制字符检查。"""
    if not isinstance(value, str):
        raise ValidationError(f"{field}: 必须是字符串，收到 {type(value).__name__}")
    if len(value) > RAW_INPUT_LIMIT:
        raise ValidationError(f"{field}: 原始长度 {len(value)} 超过上限 {RAW_INPUT_LIMIT}")
    for _ in range(MAX_NORMALIZE_ROUNDS):
        decoded = _decode_once(value)
        if decoded == value:
            break
        value = decoded
    value = unicodedata.normalize("NFKC", value)
    for ch in value:
        code = ord(ch)
        if code < 0x20 or 0x7F <= code <= 0x9F:
            raise ValidationError(f"{field}: 含控制字符 U+{code:04X}")
    return value


# ---------------------------------------------------------------------------
# 各类型白名单规则
# ---------------------------------------------------------------------------

_RE_USERNAME = re.compile(r"[a-z][a-z0-9_]{2,19}")
_RE_SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+){0,7}")
_RE_FILENAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,53}\.[a-z0-9]{1,10}")
_RE_PATH_SEGMENT = re.compile(r"[a-z0-9][a-z0-9._~-]{0,63}")
_RE_EMAIL_LOCAL = re.compile(r"[a-z0-9._%+-]{1,64}")
_RE_EMAIL_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_RE_EMAIL_TLD = re.compile(r"[a-z]{2,24}")
_RE_INTEGER = re.compile(r"-?[0-9]{1,19}")
_RE_JSON_KEY = re.compile(r"[a-z0-9_]{1,64}")

_RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {
    f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10)
}
INT64_MIN, INT64_MAX = -(2 ** 63), 2 ** 63 - 1


def _validate_username(v):
    if not _RE_USERNAME.fullmatch(v):
        raise ValidationError(
            "username: 须为小写字母开头、3-20 位的小写字母/数字/下划线")
    return v


def _validate_slug(v):
    if not (1 <= len(v) <= 64) or not _RE_SLUG.fullmatch(v):
        raise ValidationError("slug: 须为 1-64 位小写字母/数字，可用单个连字符分段")
    return v


def _validate_filename(v):
    if not (1 <= len(v) <= 64) or not _RE_FILENAME.fullmatch(v):
        raise ValidationError(
            "filename: 须为 1-64 位小写字母/数字/_/-，恰好一个扩展名，不得以点开头")
    stem = v.split(".", 1)[0]
    if stem in _RESERVED_NAMES:
        raise ValidationError(f"filename: {stem!r} 是保留设备名")
    return v


def _validate_rel_path(v):
    if not (1 <= len(v) <= 256):
        raise ValidationError("rel_path: 长度须为 1-256")
    if v.startswith("/") or v.endswith("/"):
        raise ValidationError("rel_path: 不得以 / 开头或结尾")
    segments = v.split("/")
    if not (1 <= len(segments) <= 5):
        raise ValidationError("rel_path: 目录深度须为 1-5")
    for seg in segments:
        if seg in (".", "..") or not _RE_PATH_SEGMENT.fullmatch(seg):
            raise ValidationError(f"rel_path: 非法路径段 {seg!r}")
    return v


def _validate_email(v):
    if not (3 <= len(v) <= 254):
        raise ValidationError("email: 长度须为 3-254")
    parts = v.split("@")
    if len(parts) != 2:
        raise ValidationError("email: 须恰好包含一个 @")
    local, domain = parts
    if not _RE_EMAIL_LOCAL.fullmatch(local):
        raise ValidationError("email: 本地部分字符非法")
    labels = domain.split(".")
    if len(labels) < 2:
        raise ValidationError("email: 域名至少两个标签")
    for label in labels:
        if not _RE_EMAIL_LABEL.fullmatch(label):
            raise ValidationError(f"email: 非法域名标签 {label!r}")
    if not _RE_EMAIL_TLD.fullmatch(labels[-1]):
        raise ValidationError("email: 顶级域须为 2-24 位小写字母")
    return v


def _validate_integer(v):
    if not _RE_INTEGER.fullmatch(v):
        raise ValidationError("integer: 须为可选负号加 1-19 位数字")
    if not (INT64_MIN <= int(v) <= INT64_MAX):
        raise ValidationError("integer: 超出 int64 范围")
    return v


def _reject_constant(name):
    raise ValidationError(f"json: 不允许常量 {name}")


def _validate_json(raw):
    if not isinstance(raw, str):
        raise ValidationError("json: 必须是字符串")
    if len(raw) > JSON_MAX_RAW:
        raise ValidationError(f"json: 原始长度超过 {JSON_MAX_RAW}")
    try:
        data = json.loads(raw, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"json: 解析失败: {exc}") from exc

    nodes = 0

    def walk(node, depth):
        nonlocal nodes
        nodes += 1
        if nodes > JSON_MAX_NODES:
            raise ValidationError(f"json: 节点数超过 {JSON_MAX_NODES}")
        if depth > JSON_MAX_DEPTH:
            raise ValidationError(f"json: 嵌套深度超过 {JSON_MAX_DEPTH}")
        if isinstance(node, dict):
            for key, val in node.items():
                norm_key = normalize(key, "json.key")
                if not _RE_JSON_KEY.fullmatch(norm_key):
                    raise ValidationError(f"json: 非法键名 {norm_key!r}")
                walk(val, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
        elif isinstance(node, str):
            if len(normalize(node, "json.value")) > JSON_MAX_STRING:
                raise ValidationError(f"json: 字符串值超过 {JSON_MAX_STRING} 字符")
        elif isinstance(node, bool) or node is None:
            pass
        elif isinstance(node, (int, float)):
            if isinstance(node, float) and not math.isfinite(node):
                raise ValidationError("json: 不允许非有限浮点数")
        else:
            raise ValidationError(f"json: 不允许的类型 {type(node).__name__}")

    walk(data, 1)
    return raw


# ---------------------------------------------------------------------------
# 规则表与统一入口
# ---------------------------------------------------------------------------

RULES = [
    {"kind": "username", "charset": "a-z 0-9 _",
     "length": "3-20", "structure": "必须以小写字母开头"},
    {"kind": "slug", "charset": "a-z 0-9 -",
     "length": "1-64", "structure": "连字符仅作分段，不得开头/结尾/连续，最多 8 段"},
    {"kind": "filename", "charset": "a-z 0-9 _ - .",
     "length": "1-64", "structure": "恰好一个扩展名；不得以点开头；排除 Windows 保留设备名"},
    {"kind": "rel_path", "charset": "a-z 0-9 _ - . ~ /",
     "length": "1-256", "structure": "相对路径；深度 1-5；每段 1-64；禁止 . 和 .. 段；不得以 / 开头结尾"},
    {"kind": "email", "charset": "本地: a-z 0-9 . _ % + - ；域名: a-z 0-9 - .",
     "length": "3-254（本地 ≤64，标签 ≤63）",
     "structure": "恰好一个 @；域名 ≥2 个标签；顶级域为 2-24 位小写字母；标签不得以 - 开头结尾"},
    {"kind": "integer", "charset": "0-9 与前导 -",
     "length": "1-19 位数字", "structure": "取值须在 int64 范围内"},
    {"kind": "json", "charset": "键: a-z 0-9 _；字符串值: 无控制字符",
     "length": "原始 ≤16384；键 1-64；字符串值 ≤256",
     "structure": "嵌套深度 ≤8；节点数 ≤1000；仅对象/数组/字符串/有限数字/布尔/null"},
]

VALIDATORS = {
    "username": _validate_username,
    "slug": _validate_slug,
    "filename": _validate_filename,
    "rel_path": _validate_rel_path,
    "email": _validate_email,
    "integer": _validate_integer,
    "json": _validate_json,
}


def validate(kind, value):
    """校验入口：先规范化，再按白名单规则判断。通过则返回规范形式。"""
    try:
        validator = VALIDATORS[kind]
    except KeyError:
        raise ValidationError(f"未知输入类型 {kind!r}，可选: {sorted(VALIDATORS)}")
    if kind == "json":
        return validator(value)  # JSON 对键与字符串值逐个规范化
    return validator(normalize(value, kind))
