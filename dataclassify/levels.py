"""分级级别定义（数值越大越敏感）。"""

LEVELS = {
    "L1": "公开",
    "L2": "内部",
    "L3": "敏感",
    "L4": "高度敏感",
}

_ORDER = ["L1", "L2", "L3", "L4"]


def level_rank(level: str) -> int:
    """返回级别的数值权重，未知级别返回 -1。"""
    try:
        return _ORDER.index(level)
    except ValueError:
        return -1
