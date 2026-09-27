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
    -> 去掉非间距记号(Mn) -> NFC 重新合成

说明：默认去掉变音记号（accent-insensitive，"café" == "cafe"），
这是为了满足“带变音符号的字符与去音符形式视为同键”的需求；该行为
在 MIGRATION.md 中明确标注，属于会改变等价类的决策。
"""

from __future__ import annotations

import unicodedata

DEFAULT_LOCALE = "root"
SUPPORTED_LOCALES = ("root", "tr")


def _strip_marks(text: str) -> str:
    """去掉组合记号（Unicode 类别 Mn），基字母保留。"""
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


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

    def __init__(self, locale: str = DEFAULT_LOCALE) -> None:
        if locale not in SUPPORTED_LOCALES:
            raise ValueError(
                f"不支持的区域 {locale!r}，可选：{', '.join(SUPPORTED_LOCALES)}"
            )
        self.locale = locale
        self._fold = _FOLDERS[locale]

    def normalize(self, key: str) -> str:
        if not isinstance(key, str):
            raise TypeError(f"键必须是 str，收到 {type(key).__name__}")
        # 1) 兼容分解/合成：全角->半角、合字、兼容形式全部统一。
        canonical = unicodedata.normalize("NFKC", key)
        # 2) 区域相关的大小写折叠。
        folded = self._fold(canonical)
        # 3) NFD 分解后去掉组合变音记号（é 的重音、分音符等）。
        #    必须先分解：预组合的 é(U+00E9) 本身是 Ll，直接过滤 Mn 去不掉。
        decomposed = unicodedata.normalize("NFD", folded)
        stripped = _strip_marks(decomposed)
        # 4) 重新合成，输出稳定形式。
        return unicodedata.normalize("NFC", stripped)

    def equal(self, a: str, b: str) -> bool:
        return self.normalize(a) == self.normalize(b)


def normalize_key(key: str, locale: str = DEFAULT_LOCALE) -> str:
    return KeyNormalizer(locale).normalize(key)




def keys_equal(a: str, b: str, locale: str = DEFAULT_LOCALE) -> bool:
    return KeyNormalizer(locale).equal(a, b)
