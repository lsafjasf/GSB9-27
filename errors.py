"""共享错误类型：遗留代码与重构后代码共用。"""


class ServiceError(Exception):
    """所有远程调用错误的基类。"""


class TransientError(ServiceError):
    """暂时性错误：网络超时、连接重置、5xx。可重试。"""


class RateLimitError(ServiceError):
    """限流（429）。可重试。"""


class ValidationError(ServiceError):
    """参数非法（400）。不可重试。"""


class PermissionDeniedError(ServiceError):
    """权限不足（401/403）。不可重试。"""


class NotFoundError(ServiceError):
    """资源不存在（404）。不可重试。"""


NON_RETRYABLE_ERRORS = frozenset(
    {ValidationError, PermissionDeniedError, NotFoundError}
)
