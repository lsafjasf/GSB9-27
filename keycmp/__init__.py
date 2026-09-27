"""区域可配置的键比较（字典查找 / 去重场景）。

公开接口：
    normalize_key(key, locale="root")
    keys_equal(a, b, locale="root")
    KeyNormalizer(locale="root")

只依赖 Python 3 标准库（unicodedata）。
"""

from .normalizer import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    KeyNormalizer,
    keys_equal,
    normalize_key,
)

__all__ = [
    "DEFAULT_LOCALE",
    "SUPPORTED_LOCALES",
    "KeyNormalizer",
    "keys_equal",
    "normalize_key",
]
