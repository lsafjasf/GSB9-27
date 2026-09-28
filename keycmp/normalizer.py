"""修复后的键规范化与比较实现。

旧实现只做 ``key.lower()``，因此以下“看起来一样”的键会被判成不同：

1. 大小写变体：     "ABC"  vs "abc"            （lower 能处理）
2. 全角/半角：      "ABC123"（U+FF21...）vs "abc123"
3. 组合字符：       "e\\u0301"（e + 组合重音）vs "\\u00e9"（单个 é）

修复思路：把“比较”改为对“规范化形式”比较，规范化是一个确定性映射
``key -> str``，因此自反、对称、传递三条等价关系性质由构造保证。

规范化流水线（顺序固定）：
    NFKC 兼容分解/合成 -> 区域特有的大小写折叠 -> NFD 规范分解
    -> （可选，默认关闭）按基字符文字系统删除组合记号 -> NFC 重新合成

关于“去记号”（strip_marks）
--------------------------
旧版本在第 4 步无条件删除 Unicode 类别 Mn 的所有字符。这是
语言无关（script-agnostic）的过度合并：

* 日文："が"(U+304C) NFD 后是 "か" + U+3099（浊音符，类别 Mn），
  浊音符被删后 "が" 与 "か" 规范成同一个串；半浊音 "ぱ"/"は" 同理。
* 拉丁："café" 被去音符成 "cafe"，把不同的词并成同一个键。

题意只要求统一“看起来一样”的编码变体（NFC 会把 e + U+0301 重新合成
成 é，所以组合形式 vs 预组合形式本来就等价，无需删记号）。因此去记号
默认关闭（``strip_marks=None``），需要 accent-insensitive 时按语言显式
开启，且只剥离挂在“已开启文字系统的基字符”后面的记号，假名的浊音/
半浊音符永远不会被误删：

* ``strip_marks=None``  （默认）保留全部记号；
* ``strip_marks="latin"`` 仅剥离拉丁基字符上的记号（café -> cafe，
  が 仍与 か 不同）；
* ``strip_marks="all"``  剥离所有基字符上的记号（旧行为，不推荐，
  会把 が/か 合并，仅为兼容保留）；
* 也可传入可迭代的脚本名（如 ``{"latin", "greek"}``）自行限定。
"""

from __future__ import annotations

import unicodedata

DEFAULT_LOCALE = "root"
SUPPORTED_LOCALES = ("root", "tr")

# 去记号策略；None = 不去记号（默认）。
DEFAULT_STRIP_MARKS: str | None = None
# 预设策略名 -> 允许剥离记号的“基字符文字系统”集合。"all" 是显式通配，
# 不展开成集合，而是在剥离时跳过文字检查（包含所有脚本）。
MARK_STRIP_PRESETS = {
    "latin": frozenset({"latin"}),
    "all": "all",
}


def _script_of(ch: str) -> str | None:
    """返回一个“基字符”所属的文字系统（粗粒度，仅覆盖去记号需要的范围）。

    组合记号（Mn）不调用本函数；返回 None 表示不在已识别范围内，
    其上的记号默认不剥离。
    """
    cp = ord(ch)
    if 0x0041 <= cp <= 0x005A or 0x0061 <= cp <= 0x007A:
        return "latin"
    # Latin-1 Supplement、Latin Extended-A/B、IPA Extensions、
    # Spacing Modifier Letters（casefold 后拉丁基字母仍落在这些区）。
    if 0x00AA <= cp <= 0x02AF or 0x1E00 <= cp <= 0x1EFF:
        return "latin"
    if 0x0370 <= cp <= 0x03FF or 0x1F00 <= cp <= 0x1FFF:
        return "greek"
    if 0x0400 <= cp <= 0x04FF or 0x0500 <= cp <= 0x052F:
        return "cyrillic"
    # 平假名（含 U+3095/U+3096）、片假名（含 U+30F5/U+30F6、
    # U+30FC 长音符）。浊音/半浊音基字符必须识别为 kana，
    # 这样挂在它们后面的 U+3099/U+309A 才永远不会被剥离。
    if 0x3041 <= cp <= 0x3096 or 0x30A1 <= cp <= 0x30FA:
        return "kana"
    return None


def _coerce_strip_policy(strip_marks) -> frozenset | str | None:
    if strip_marks is None:
        return None
    if isinstance(strip_marks, str):
        if strip_marks in MARK_STRIP_PRESETS:
            return MARK_STRIP_PRESETS[strip_marks]
        raise ValueError(
            f"未知的去记号预设 {strip_marks!r}，"
            f"可选：None、{', '.join(sorted(MARK_STRIP_PRESETS))}，"
            f"或脚本名可迭代（如 ('latin', 'greek')）"
        )
    try:
        scripts = frozenset(strip_marks)
    except TypeError as exc:
        raise TypeError(
            "strip_marks 必须是 None、预设字符串，或脚本名可迭代"
        ) from exc
    if not scripts or not all(isinstance(s, str) and s for s in scripts):
        raise ValueError("strip_marks 脚本名集合不能为空，且必须是非空字符串")
    return scripts


def _strip_marks(text: str, allowed_scripts: frozenset | str) -> str:
    """删除组合记号（Unicode 类别 Mn），但按基字符的文字系统决定。

    只有挂在“已开启文字系统的基字符”后面的记号才删除；孤立记号或
    挂在未开启文字系统（如假名）上的记号保留。``allowed_scripts``
    为字符串 ``"all"`` 时退化为旧的无条件删除（不推荐）。
    """
    if allowed_scripts == "all":
        return "".join(ch for ch in text
                       if unicodedata.category(ch) != "Mn")

    out: list[str] = []
    base: str | None = None
    for ch in text:
        if unicodedata.category(ch) == "Mn":
            if base is not None and _script_of(base) in allowed_scripts:
                continue  # 删除：挂在已开启脚本基字符上的记号
            out.append(ch)  # 保留：假名浊音符等
        else:
            base = ch
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

        norm = KeyNormalizer()                       # 默认不剥离任何记号
        norm = KeyNormalizer(strip_marks="latin")    # 仅拉丁去音符
        norm = KeyNormalizer(locale="tr", strip_marks="latin")
    """

    def __init__(
        self,
        locale: str = DEFAULT_LOCALE,
        strip_marks=DEFAULT_STRIP_MARKS,
    ) -> None:
        if locale not in SUPPORTED_LOCALES:
            raise ValueError(
                f"不支持的区域 {locale!r}，可选：{', '.join(SUPPORTED_LOCALES)}"
            )
        self.locale = locale
        self.strip_marks = strip_marks
        self._allowed_scripts = _coerce_strip_policy(strip_marks)
        self._fold = _FOLDERS[locale]

    def normalize(self, key: str) -> str:
        if not isinstance(key, str):
            raise TypeError(f"键必须是 str，收到 {type(key).__name__}")
        # 1) 兼容分解/合成：全角->半角、合字、兼容形式全部统一。
        canonical = unicodedata.normalize("NFKC", key)
        # 2) 区域相关的大小写折叠。
        folded = self._fold(canonical)
        # 3) NFD 规范分解：预组合的 é(U+00E9) 拆成 e + U+0301；
        #    が(U+304C) 拆成 か + U+3099（浊音符）。
        decomposed = unicodedata.normalize("NFD", folded)
        # 4) 可选地按语言（基字符文字系统）剥离组合记号。默认不剥离，
        #    所以 が 与 か、café 与 cafe 都保持不同。
        if self._allowed_scripts is not None:
            decomposed = _strip_marks(decomposed, self._allowed_scripts)
        # 5) 重新合成，输出稳定形式：e + U+0301 -> é，
        #    か + U+3099 -> が（记号保留时）。
        return unicodedata.normalize("NFC", decomposed)

    def equal(self, a: str, b: str) -> bool:
        return self.normalize(a) == self.normalize(b)


def normalize_key(
    key: str,
    locale: str = DEFAULT_LOCALE,
    strip_marks=DEFAULT_STRIP_MARKS,
) -> str:
    return KeyNormalizer(locale, strip_marks=strip_marks).normalize(key)


def keys_equal(
    a: str,
    b: str,
    locale: str = DEFAULT_LOCALE,
    strip_marks=DEFAULT_STRIP_MARKS,
) -> bool:
    return KeyNormalizer(locale, strip_marks=strip_marks).equal(a, b)
