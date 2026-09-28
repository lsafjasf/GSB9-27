"""修复后的键规范化与比较实现。

旧实现只做 ``key.lower()``，因此以下“看起来一样”的键会被判成不同：

1. 大小写变体：     "ABC"  vs "abc"            （lower 能处理）
2. 全角/半角：      "ABC123"（U+FF21...）vs "abc123"
3. 组合字符：       "e\\u0301"（e + 组合重音）vs "\\u00e9"（单个 é）
4. 带变音符号：     "caf\\u00e9" vs "cafe\\u0301"（重音是否保留 / 组合形式不同）

修复思路：把“比较”改为对“规范化形式”比较，规范化是一个确定性映射
``key -> str``，因此自反、对称、传递三条等价关系性质由构造保证。

规范化流水线（顺序固定）：
    NFKC 兼容分解/合成 -> 区域特有的大小写折叠 -> NFD 规范分解
    -> （可选）按语言范围去掉组合记号(Mn) -> NFC 重新合成

说明：去记号（strip_marks）是显式可选项，默认 "none" 不删任何记号，
只处理题面点名的现象（大小写、全角/半角、预组合 vs 组合写法）。
历史上默认无条件删除全部 Mn 记号，会把不同文字错误合并：
  - 日文 が(か+U+3099 浊点) 被并成 か；
  - 西里尔 й(и+U+0306) 被并成 и；
  - 拉丁 café 与 cafe 被并成同键（accent-insensitive）。
现在这些行为只能通过 strip_marks="latin"（仅拉丁基字母上的记号）
或 strip_marks="all"（旧行为，全部 Mn）显式开启。
"""

from __future__ import annotations

import unicodedata

DEFAULT_LOCALE = "root"
SUPPORTED_LOCALES = ("root", "tr")
DEFAULT_STRIP_MARKS = "none"
STRIP_MARKS_MODES = ("none", "latin", "all")


def _is_latin_base(ch: str) -> bool:
    """判断字符是否拉丁字母（用字符名前缀，标准库内最可靠的方式）。"""
    if not ch:
        return False
    try:
        return unicodedata.name(ch).startswith("LATIN")
    except ValueError:
        return False


def _strip_marks(text: str, mode: str) -> str:
    """按语言范围去掉组合记号（Unicode 类别 Mn）。

    mode="none"  ：不删任何记号（默认）。
    mode="latin" ：只删附着在拉丁基字母上的记号（é->e、İ 的附加点），
                  日文浊点、西里尔短音符等其他文字的记号保留。
    mode="all"   ：删除全部 Mn（旧行为，跨文字合并，仅作显式兼容选项）。
    """
    if mode == "none":
        return text
    if mode == "all":
        return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    out = []
    last_base = ""
    for ch in text:
        if unicodedata.category(ch) == "Mn":
            if _is_latin_base(last_base):
                continue  # 拉丁字母上的变音记号：删
        else:
            last_base = ch
        out.append(ch)
    return "".join(out)


def _fold_root(text: str) -> str:
    # str.casefold 是 Unicode 无区域（root）大小写折叠：
    # 比 lower 更激进，例如 ß -> ss、ﬃ -> ffi。
    return text.casefold()


def _fold_tr(text: str) -> str:
    # 土耳其区域规则：
    #   İ (U+0130, 带点大写 I) -> i （而不是 root 规则下的 "i̇"）
    #   I (U+0049)             -> ı (U+0131, 无点小写 i)
    # 其余字符继续走 root 的 casefold。
    text = text.replace("\u0130", "i").replace("I", "\u0131")
    return _fold_root(text)


_FOLDERS = {
    "root": _fold_root,
    "tr": _fold_tr,
}


class KeyNormalizer:
    """把键映射为用于查找/去重的规范化形式。

    用法::

        norm = KeyNormalizer(locale="tr")
        norm.normalize("İstanbul") == norm.normalize("istanbul")
    """

    def __init__(self, locale: str = DEFAULT_LOCALE,
                 strip_marks: str = DEFAULT_STRIP_MARKS) -> None:
        if locale not in SUPPORTED_LOCALES:
            raise ValueError(
                f"不支持的区域 {locale!r}，可选：{', '.join(SUPPORTED_LOCALES)}"
            )
        if strip_marks not in STRIP_MARKS_MODES:
            raise ValueError(
                f"不支持的 strip_marks {strip_marks!r}，可选："
                f"{', '.join(STRIP_MARKS_MODES)}"
            )
        self.locale = locale
        self.strip_marks = strip_marks
        self._fold = _FOLDERS[locale]

    def normalize(self, key: str) -> str:
        if not isinstance(key, str):
            raise TypeError(f"键必须是 str，收到 {type(key).__name__}")
        # 1) 兼容分解/合成：全角->半角、合字、兼容形式全部统一。
        canonical = unicodedata.normalize("NFKC", key)
        # 2) 区域相关的大小写折叠。
        folded = self._fold(canonical)
        # 3) NFD 分解后按配置去组合记号。必须先分解：预组合的 é(U+00E9)
        #    本身是 Ll，直接过滤 Mn 去不掉。默认 "none" 时此步只保证
        #    预组合与组合写法落到同一规范形式，不删任何记号。
        decomposed = unicodedata.normalize("NFD", folded)
        stripped = _strip_marks(decomposed, self.strip_marks)
        # 4) 重新合成，输出稳定形式。
        return unicodedata.normalize("NFC", stripped)

    def equal(self, a: str, b: str) -> bool:
        return self.normalize(a) == self.normalize(b)


def normalize_key(key: str, locale: str = DEFAULT_LOCALE,
                  strip_marks: str = DEFAULT_STRIP_MARKS) -> str:
    return KeyNormalizer(locale, strip_marks=strip_marks).normalize(key)




def keys_equal(a: str, b: str, locale: str = DEFAULT_LOCALE,
               strip_marks: str = DEFAULT_STRIP_MARKS) -> bool:
    return KeyNormalizer(locale, strip_marks=strip_marks).equal(a, b)
