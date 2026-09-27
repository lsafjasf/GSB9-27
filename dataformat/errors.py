class MigrationError(Exception):
    """迁移相关错误的基类。"""


class MissingFieldError(MigrationError):
    """必填字段缺失：显式报错，绝不用默认值掩盖。"""

    def __init__(self, path, version):
        self.path = path
        self.version = version
        super().__init__(
            f"必填字段缺失: {path!r} (schema v{version})。"
            "请补齐字段或显式声明使用默认值，不会静默填充。"
        )


class UnknownVersionError(MigrationError):
    pass
