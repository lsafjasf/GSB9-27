"""unicode_guard —— 用户名/黑名单的 Unicode 归一化与同形字符安全检查库（仅标准库）。

三种形式与使用环节（详见 README.md）：
  * 展示形式 display_form(s) : NFC 归一化后的原文，仅用于界面展示。
  * 存储形式 storage_form(s) : NFC 原文入库，保留用户原始拼写，绝不用于比较。
  * 比较形式 compare_key(s)  : NFKC -> casefold -> 同形骨架映射 -> 剔除不可见字符 -> NFC，
                               是唯一允许用于黑名单比对 / 唯一性判定的键。

安全检查 scan()/inspect() 独立于比较键，负责发现：
  * 同形混用（拉丁/西里尔/希腊字母混排）   MIXED_SCRIPT
  * 不可见字符（零宽、Cf 格式符、标签字符） INVISIBLE
  * 变体选择符                             VARIATION_SELECTOR
  * 双向文本控制符                          BIDI_CONTROL
  * 非字符码位                              NONCHARACTER
  * 游离组合记号（开头无基字）              DANGLING_MARK
  * 兼容性折叠会改变字符串（信息级）        FOLDED
每类发现都可通过 policy 配置为 REJECT / WARN / REPLACE。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# 策略与发现类型
# ---------------------------------------------------------------------------


class Action(Enum):
    REJECT = "reject"    # 拒绝：视为非法输入，inspect() 抛 SafetyError
    WARN = "warn"        # 告警：放行并记录（比较键仍按安全规则计算）
    REPLACE = "replace"  # 替换：剔除/替换危险字符后继续（并记录告警）


class FindingType(Enum):
    MIXED_SCRIPT = "mixed_script"
    INVISIBLE = "invisible"
    VARIATION_SELECTOR = "variation_selector"
    BIDI_CONTROL = "bidi_control"
    NONCHARACTER = "noncharacter"
    DANGLING_MARK = "dangling_mark"
    FOLDED = "folded"


#: 默认策略：同形混用与 bidi 控制拒绝；不可见字符与变体选择符替换剔除；其余告警。
DEFAULT_POLICY = {
    FindingType.MIXED_SCRIPT: Action.REJECT,
    FindingType.INVISIBLE: Action.REPLACE,
    FindingType.VARIATION_SELECTOR: Action.REPLACE,
    FindingType.BIDI_CONTROL: Action.REJECT,
    FindingType.NONCHARACTER: Action.WARN,
    FindingType.DANGLING_MARK: Action.WARN,
    FindingType.FOLDED: Action.WARN,
}


@dataclass(frozen=True)
class Finding:
    type: FindingType
    index: int    # 触发字符在原串中的下标
    char: str     # 触发字符（FOLDED 为空串）
    detail: str

    def describe(self) -> str:
        if self.char:
            cps = " ".join(f"U+{ord(c):04X}" for c in self.char)
            name = unicodedata.name(self.char[0], "<unnamed>")
            return f"{self.type.value} @{self.index} [{cps}] {name} :: {self.detail}"
        return f"{self.type.value} :: {self.detail}"


@dataclass
class Inspection:
    original: str
    sanitized: str                              # 应用 REPLACE 后的字符串
    findings: list = field(default_factory=list)        # list[Finding]
    rejected: bool = False
    reject_reasons: list = field(default_factory=list)  # list[Finding]
    warnings: list = field(default_factory=list)        # list[Finding]
    compare_key: str = ""
    blacklisted: bool = False


class SafetyError(ValueError):
    """inspect() 命中 REJECT 策略时抛出，携带完整 Inspection。"""

    def __init__(self, inspection: Inspection):
        self.inspection = inspection
        msg = "; ".join(f.describe() for f in inspection.reject_reasons)
        super().__init__(f"rejected by policy: {msg}")


# ---------------------------------------------------------------------------
# 同形骨架映射（UTS#39 confusables 精选子集，键一律小写；可按需扩充）
# ---------------------------------------------------------------------------

CONFUSABLES = {
    # 西里尔 -> 拉丁
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y",
    "х": "x", "ѕ": "s", "м": "m", "і": "i", "ј": "j", "ӏ": "l", "һ": "h",
    "ԁ": "d", "ԍ": "g", "ո": "n", "т": "t", "к": "k", "в": "b",
    # 希腊 -> 拉丁
    "α": "a", "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
    "ω": "w", "κ": "k", "ν": "v", "ε": "e", "ι": "i", "η": "n",
    # 小型大写 / 修饰字母 -> 拉丁
    "ʙ": "b", "ɢ": "g", "ʜ": "h", "ɴ": "n", "ʀ": "r", "ᴍ": "m",
}


# ---------------------------------------------------------------------------
# 码位分类
# ---------------------------------------------------------------------------

_BIDI_RANGES = ((0x202A, 0x202E), (0x2066, 0x2069))
_VARIATION_RANGES = ((0xFE00, 0xFE0F), (0xE0100, 0xE01EF))


def _in_ranges(cp: int, ranges) -> bool:
    return any(lo <= cp <= hi for lo, hi in ranges)


def is_bidi_control(ch: str) -> bool:
    return _in_ranges(ord(ch), _BIDI_RANGES)


def is_variation_selector(ch: str) -> bool:
    return _in_ranges(ord(ch), _VARIATION_RANGES)


def is_noncharacter(ch: str) -> bool:
    cp = ord(ch)
    return 0xFDD0 <= cp <= 0xFDEF or (cp & 0xFFFE) == 0xFFFE


def is_invisible(ch: str) -> bool:
    """零宽字符、Cf 格式控制符、标签字符等；不含 bidi 控制与变体选择符。"""
    if is_bidi_control(ch) or is_variation_selector(ch):
        return False
    return unicodedata.category(ch) == "Cf"


def is_ignorable_for_compare(ch: str) -> bool:
    """比较键中一律剔除的字符：不可见字符、变体选择符、bidi 控制、非字符。"""
    return (is_invisible(ch) or is_variation_selector(ch)
            or is_bidi_control(ch) or is_noncharacter(ch))


def script_of(ch: str) -> str:
    """粗粒度文字系统划分：LATIN / CYRILLIC / GREEK / MARK / COMMON / OTHER。"""
    cp = ord(ch)
    cat = unicodedata.category(ch)
    if cat.startswith("M"):
        return "MARK"      # 组合记号，继承前一个基字的文字系统
    if not cat.startswith("L"):
        return "COMMON"    # 数字、标点、符号、空白不参与混排判定
    if cp < 0x80 or 0x00C0 <= cp <= 0x024F or 0x1E00 <= cp <= 0x1EFF:
        return "LATIN"
    if 0x0400 <= cp <= 0x052F or 0x2DE0 <= cp <= 0x2DFF or 0xA640 <= cp <= 0xA69F:
        return "CYRILLIC"
    if 0x0370 <= cp <= 0x03FF or 0x1F00 <= cp <= 0x1FFF:
        return "GREEK"
    return "OTHER"


# ---------------------------------------------------------------------------
# 扫描（只读，不修改输入）
# ---------------------------------------------------------------------------


def scan(s: str) -> list:
    """返回 list[Finding]。"""
    findings = []
    scripts = set()
    first_of_script = {}
    last_script = None
    mixed_reported = False
    seen_base = False

    for i, ch in enumerate(s):
        if is_noncharacter(ch):
            findings.append(Finding(FindingType.NONCHARACTER, i, ch,
                                    "Unicode 非字符码位（noncharacter）"))
            continue
        if is_bidi_control(ch):
            findings.append(Finding(FindingType.BIDI_CONTROL, i, ch,
                                    "双向文本控制符，可制造视觉欺骗"))
            continue
        if is_variation_selector(ch):
            findings.append(Finding(FindingType.VARIATION_SELECTOR, i, ch,
                                    "变体选择符，渲染不可见或改变字形"))
            continue
        if is_invisible(ch):
            findings.append(Finding(FindingType.INVISIBLE, i, ch,
                                    "不可见格式字符（零宽/控制/标签）"))
            continue

        if unicodedata.category(ch).startswith("M"):
            if not seen_base:
                findings.append(Finding(FindingType.DANGLING_MARK, i, ch,
                                        "组合记号前没有基字"))
            continue

        seen_base = True
        sc = script_of(ch)
        if sc in ("COMMON", "OTHER"):
            continue
        scripts.add(sc)
        first_of_script.setdefault(sc, (i, ch))
        if not mixed_reported and last_script is not None and sc != last_script:
            # 拉丁是主要仿冒目标：报告非拉丁方的首个字符；否则报告后出现的文字
            if last_script == "LATIN":
                suspect = sc
            elif sc == "LATIN":
                suspect = last_script
            else:
                suspect = sc
            si, sch = first_of_script[suspect]
            findings.append(Finding(
                FindingType.MIXED_SCRIPT, si, sch,
                f"同形混用：{sorted(scripts)} 字母混排，"
                f"可疑字符骨架映射为 {CONFUSABLES.get(sch.lower(), sch.lower())!r}"))
            mixed_reported = True
        last_script = sc

    if unicodedata.normalize("NFKC", s) != s:
        findings.append(Finding(FindingType.FOLDED, 0, "",
                                "NFKC 兼容性折叠会改变该字符串（全角/兼容字符等）"))
    return findings


# ---------------------------------------------------------------------------
# 归一化管线
# ---------------------------------------------------------------------------


def _strip_leading_marks(s: str) -> str:
    i = 0
    while i < len(s) and unicodedata.category(s[i]).startswith("M"):
        i += 1
    return s[i:]


def compare_key(s: str) -> str:
    """比较键：NFKC -> casefold -> NFKC -> 同形骨架映射 -> 去不可见字符 -> 去游离记号 -> NFC。

    该键与 inspect() 的策略无关：即使某类字符的策略配置为 WARN，
    比较键依然剔除它们，保证等价性自洽、黑名单无法被绕过。
    """
    s = unicodedata.normalize("NFKC", s)
    s = s.casefold()
    s = unicodedata.normalize("NFKC", s)   # casefold 可能引入未归一化序列
    s = "".join(CONFUSABLES.get(ch, ch) for ch in s)
    s = "".join(ch for ch in s if not is_ignorable_for_compare(ch))
    s = _strip_leading_marks(s)
    return unicodedata.normalize("NFC", s)


def display_form(s: str) -> str:
    """展示形式：NFC 原文（渲染前建议配合 scan() 提示风险）。"""
    return unicodedata.normalize("NFC", s)


def storage_form(s: str) -> str:
    """存储形式：NFC 原文入库；比较一律使用另行计算的 compare_key。"""
    return unicodedata.normalize("NFC", s)


def equivalent(a: str, b: str) -> bool:
    return compare_key(a) == compare_key(b)


# ---------------------------------------------------------------------------
# 策略应用
# ---------------------------------------------------------------------------

_REPLACEABLE = (FindingType.INVISIBLE, FindingType.VARIATION_SELECTOR,
                FindingType.BIDI_CONTROL, FindingType.NONCHARACTER)


def _apply_replace(s: str, findings: list) -> str:
    drop = {f.index for f in findings if f.type in _REPLACEABLE}
    return "".join(ch for i, ch in enumerate(s) if i not in drop)


def inspect(s: str, policy: dict = None) -> Inspection:
    """扫描并按策略处理；任一 REJECT 命中即抛 SafetyError。"""
    policy = policy or DEFAULT_POLICY
    findings = scan(s)
    result = Inspection(original=s, sanitized=s, findings=findings)

    replace_targets = []
    for f in findings:
        action = policy.get(f.type, Action.WARN)
        if action is Action.REJECT:
            result.rejected = True
            result.reject_reasons.append(f)
        elif action is Action.WARN:
            result.warnings.append(f)
        elif action is Action.REPLACE:
            replace_targets.append(f)
            result.warnings.append(f)

    if result.rejected:
        raise SafetyError(result)

    if replace_targets:
        result.sanitized = _apply_replace(s, replace_targets)
    result.compare_key = compare_key(result.sanitized)
    return result


# ---------------------------------------------------------------------------
# 黑名单
# ---------------------------------------------------------------------------


class UsernameBlacklist:
    """黑名单内部一律以 compare_key 存储与比对，与展示/存储形式解耦。"""

    def __init__(self, entries=(), policy: dict = None):
        self.policy = policy or DEFAULT_POLICY
        self._keys = set()
        for e in entries:
            self.add(e)

    def add(self, entry: str) -> None:
        self._keys.add(compare_key(entry))

    def is_blocked(self, username: str) -> bool:
        return compare_key(username) in self._keys

    def check(self, username: str) -> Inspection:
        """安全检查 + 黑名单判定；REJECT 命中时抛 SafetyError。"""
        insp = inspect(username, self.policy)
        insp.blacklisted = self.is_blocked(insp.sanitized)
        return insp
