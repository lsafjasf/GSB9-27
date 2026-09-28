"""unicode_guard: Unicode 归一化与同形/不可见字符安全检查（仅标准库）。

三个环节的形式约定（详见 STRATEGY.md）：
  - 比较 (comparison): canonical_key()  —— 剥离不可见字符 + NFKC + casefold + 同形骨架映射
  - 存储 (storage):    storage_form()   —— 剥离不可见字符后的 NFC（保留用户原始可见字符）
  - 展示 (display):    display_form()   —— 原始输入的 NFC（不做任何删除/折叠）
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class Policy(Enum):
    """对某类风险字符的处理策略。"""
    REJECT = "reject"    # 检出即拒绝（报告 ok=False）
    WARN = "warn"        # 检出但放行（报告 ok=True，带告警）
    REPLACE = "replace"  # 检出并替换/剥离后放行（sanitized 字段给出清洗结果）


class IssueKind(Enum):
    INVISIBLE = "invisible"          # 零宽/控制/标签等不可见字符
    CONFUSABLE = "confusable"        # 与目标字符视觉相同的异码位字符
    MIXED_SCRIPT = "mixed_script"    # 同一标识符内混用多个文字系统
    NON_NFC = "non_nfc"              # 非规范组合顺序（可被规范化检出）


@dataclass
class Issue:
    kind: IssueKind
    position: int          # 在原始字符串中的下标（MIXED_SCRIPT 为 -1）
    char: str              # 相关字符（MIXED_SCRIPT 为空串）
    codepoint: str
    name: str
    detail: str = ""


@dataclass
class Config:
    invisible_policy: Policy = Policy.REJECT
    confusable_policy: Policy = Policy.WARN
    mixed_script_policy: Policy = Policy.WARN
    # 允许的文字系统组合白名单之外的混用才告警；COMMON 类（数字/标点）不参与
    allowed_scripts: Tuple[str, ...] = ("LATIN",)


@dataclass
class Report:
    original: str
    ok: bool                       # 是否放行（无任何 REJECT 命中的问题）
    issues: List[Issue] = field(default_factory=list)
    sanitized: str = ""            # REPLACE 策略下的清洗结果
    canonical: str = ""            # 比较用键
    storage: str = ""              # 存储用形式
    display: str = ""              # 展示用形式


# ---------------------------------------------------------------------------
# 不可见字符
# ---------------------------------------------------------------------------

def is_invisible(ch: str) -> bool:
    """零宽、方向控制、标签、变体选择符、控制字符（\\t\\n\\r 除外）。"""
    if ch in ("\t", "\n", "\r"):
        return False
    cp = ord(ch)
    if unicodedata.category(ch) in ("Cf", "Cc"):
        return True
    if 0xFE00 <= cp <= 0xFE0F:      # 变体选择符 VS1-VS16
        return True
    if 0xE0000 <= cp <= 0xE007F:    # 标签字符
        return True
    if cp == 0x00AD:                # SOFT HYPHEN（属 Cf，冗余保险）
        return True
    return False


def strip_invisible(text: str) -> str:
    return "".join(ch for ch in text if not is_invisible(ch))


# ---------------------------------------------------------------------------
# 同形字符骨架映射（精选子集：常见西里尔/希腊/其他伪装成拉丁的字符）
# ---------------------------------------------------------------------------

_CONFUSABLE_PAIRS = [
    # 西里尔小写 -> 拉丁
    "аa", "еe", "оo", "рp", "сc", "хx", "уy", "іi", "јj", "ѕs",
    "һh", "ԁd", "ԛq", "ԝw", "вb", "нh", "кk", "мm", "тt", "ꓒo",
    # 西里尔大写 -> 拉丁
    "АA", "ВB", "ЕE", "КK", "МM", "НH", "ОO", "РP", "СC", "ТT", "ХX",
    # 希腊小写 -> 拉丁
    "αa", "βb", "γy", "δd", "εe", "ζz", "ηn", "ιi", "κk", "μu",
    "νv", "οo", "ρp", "τt", "υu", "χx", "ωw",
    # 希腊大写 -> 拉丁
    "ΑA", "ΒB", "ΕE", "ΖZ", "ΗH", "ΙI", "ΚK", "ΜM", "ΝN", "ΟO",
    "ΡP", "ΤT", "ΥY", "ΧX",
    # 其他常见伪装
    "ıi",   # 土耳其无点 i
    "ℓl",   # 手写体 l
    "οo",   # 希腊 omicron（冗余保险）
    "ɑa", "ɡg", "ʙb",
    "０0", "１1",  # 全角数字（NFKC 也会处理，冗余保险）
]

CONFUSABLES: Dict[str, str] = {}
for _pair in _CONFUSABLE_PAIRS:
    if len(_pair) == 2:
        CONFUSABLES[_pair[0]] = _pair[1]

# 反向集合：所有“看起来像拉丁字母”的非拉丁字符
LOOKALIKE_CHARS = frozenset(CONFUSABLES.keys())


def is_confusable(ch: str) -> bool:
    return ch in CONFUSABLES


def skeleton(text: str) -> str:
    """同形骨架：把已知伪装字符映射为其视觉对应的拉丁字符。"""
    return "".join(CONFUSABLES.get(ch, ch) for ch in text)


# ---------------------------------------------------------------------------
# 文字系统（script）粗分类：基于 Unicode 字符名前缀
# ---------------------------------------------------------------------------

_SCRIPT_PREFIXES = (
    "LATIN", "CYRILLIC", "GREEK", "CJK", "HIRAGANA", "KATAKANA",
    "HANGUL", "ARABIC", "HEBREW", "CYRILLIC", "ARMENIAN", "GEORGIAN",
    "DEVANAGARI", "THAI",
)


def script_of(ch: str) -> str:
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return "UNKNOWN"
    for prefix in _SCRIPT_PREFIXES:
        if name.startswith(prefix + " "):
            return prefix
    return "COMMON"  # 数字、标点、符号等


# ---------------------------------------------------------------------------
# 三种形式
# ---------------------------------------------------------------------------

def canonical_key(text: str) -> str:
    """比较用键：去不可见 -> NFKC -> casefold -> NFKC -> 同形骨架 -> NFC。

    等价性定义：a ≡ b 当且仅当 canonical_key(a) == canonical_key(b)。
    """
    cleaned = strip_invisible(text)
    folded = unicodedata.normalize("NFKC", cleaned).casefold()
    folded = unicodedata.normalize("NFKC", folded)  # casefold 可能引入新组合
    return unicodedata.normalize("NFC", skeleton(folded))


def storage_form(text: str) -> str:
    """存储用形式：剥离不可见字符后的 NFC（保留用户可见字符原貌）。"""
    return unicodedata.normalize("NFC", strip_invisible(text))


def display_form(text: str) -> str:
    """展示用形式：原始输入的 NFC，不删除、不折叠。"""
    return unicodedata.normalize("NFC", text)


def equivalent(a: str, b: str) -> bool:
    return canonical_key(a) == canonical_key(b)


# ---------------------------------------------------------------------------
# 检查入口
# ---------------------------------------------------------------------------

def analyze(text: str, config: Optional[Config] = None) -> Report:
    cfg = config or Config()
    issues: List[Issue] = []

    for idx, ch in enumerate(text):
        cp = "U+%04X" % ord(ch)
        name = unicodedata.name(ch, "<unnamed>")
        if is_invisible(ch):
            issues.append(Issue(IssueKind.INVISIBLE, idx, ch, cp, name,
                                "不可见/控制字符"))
        elif is_confusable(ch):
            issues.append(Issue(IssueKind.CONFUSABLE, idx, ch, cp, name,
                                "形似 '%s' 的伪装字符" % CONFUSABLES[ch]))

    # 混用文字系统检测（在剥离不可见字符后的可见文本上做）
    visible = strip_invisible(text)
    scripts = {script_of(ch) for ch in visible if ch.isalpha()}
    scripts.discard("COMMON")
    scripts.discard("UNKNOWN")
    foreign = scripts - set(cfg.allowed_scripts)
    if foreign and (scripts & set(cfg.allowed_scripts) or len(scripts) > 1):
        issues.append(Issue(IssueKind.MIXED_SCRIPT, -1, "", "-", "-",
                            "混用文字系统: %s" % ",".join(sorted(scripts))))

    # 规范化形式检查：非 NFC 说明存在可被调换顺序的组合字符
    if unicodedata.normalize("NFC", visible) != visible:
        issues.append(Issue(IssueKind.NON_NFC, -1, "", "-", "-",
                            "存在非规范组合顺序（NFC 后不同）"))

    # 策略裁决
    rejected = False
    for issue in issues:
        policy = {
            IssueKind.INVISIBLE: cfg.invisible_policy,
            IssueKind.CONFUSABLE: cfg.confusable_policy,
            IssueKind.MIXED_SCRIPT: cfg.mixed_script_policy,
            IssueKind.NON_NFC: Policy.REPLACE,  # 规范化即可消除，永不拒绝
        }[issue.kind]
        if policy == Policy.REJECT:
            rejected = True

    sanitized = storage_form(text)  # REPLACE 的结果：剥不可见 + NFC

    return Report(
        original=text,
        ok=not rejected,
        issues=issues,
        sanitized=sanitized,
        canonical=canonical_key(text),
        storage=storage_form(text),
        display=display_form(text),
    )
