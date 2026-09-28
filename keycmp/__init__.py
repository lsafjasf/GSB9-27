"""区域可配置的键比较（字典查找 / 去重场景）。

公开接口：
    normalize_key(key, locale="root", strip_marks="none")
    keys_equal(a, b, locale="root", strip_marks="none")
    KeyNormalizer(locale="root", strip_marks="none")

只依赖 Python 3 标准库（unicodedata）。
"""

from .normalizer import (
    DEFAULT_LOCALE,
    DEFAULT_STRIP_MARKS,
    STRIP_MARKS_MODES,
    SUPPORTED_LOCALES,
    KeyNormalizer,
    keys_equal,
    normalize_key,
)

__all__ = [
    "DEFAULT_LOCALE",
    "DEFAULT_STRIP_MARKS",
    "STRIP_MARKS_MODES",
    "SUPPORTED_LOCALES",
    "KeyNormalizer",
    "keys_equal",
    "normalize_key",
]
