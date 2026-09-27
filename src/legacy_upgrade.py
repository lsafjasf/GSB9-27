"""旧版（有缺陷）的升级逻辑 —— 仅用于复现问题，请勿在生产中使用。

缺陷：
1. 每一级升级都从零构造新字典，只拷贝"当时记得"的字段，
   未知字段（含未来版本写入的字段、用户自定义字段）被静默丢弃。
2. 缺失字段直接用默认值填充，下游无法区分"字段缺失"与"值就是默认值"。
3. 嵌套结构同样整体重建，嵌套内的新增/未知字段也会丢失。
"""

CURRENT_VERSION = 3


def upgrade(doc):
    """把任意旧版本文档升级到最新版本（有缺陷的实现）。"""
    version = doc.get("version", 1)
    data = doc
    if version == 1:
        data = _v1_to_v2(data)
        version = 2
    if version == 2:
        data = _v2_to_v3(data)
        version = 3
    return data


def _v1_to_v2(d):
    settings = d.get("settings", {})
    return {
        "version": 2,
        "name": d.get("name", ""),                # 缺失 -> 静默默认 ""
        "nickname": d.get("nick", ""),            # 改名；缺失 -> 静默默认 ""
        "email": d.get("email"),                  # 新增；静默默认 None
        "settings": {                             # 嵌套整体重建 -> 未知键丢失
            "theme": settings.get("theme", "light"),
            "font_size": settings.get("font_size", 12),
            "language": "en",                     # 新增；静默默认
        },
    }


def _v2_to_v3(d):
    settings = d.get("settings", {})
    return {
        "version": 3,
        "name": d.get("name", ""),
        "nickname": d.get("nickname", ""),
        "contact_email": d.get("email"),          # 改名 email -> contact_email
        "tags": [],                               # 新增；静默默认 []
        "settings": {
            "theme": settings.get("theme", "light"),
            "language": settings.get("language", "en"),
            "timezone": "UTC",                    # 新增；静默默认
            # font_size 在此版本被删除（静默丢弃，无任何记录）
        },
    }
