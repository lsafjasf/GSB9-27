"""共享错误分类：遗留代码与重构后代码共用同一份定义。

可重试：TransientError、RateLimitError。
禁止重试：ValidationError、PermissionDeniedError、NotFoundError
（重试不会改变结果，只会放大延迟与副作用）。
"""


class ServiceError(Exception):
    """所有远程调用错误的基类。"""


class TransientError(ServiceError):
    """暂时性错误：网络超时、连接重置、5xx。可重试。"""


class RateLimitError(ServiceError):
    """限流（HTTP 429）。可重试。"""


class ValidationError(ServiceError):
    """参数非法（HTTP 400）。禁止重试。"""


class PermissionDeniedError(ServiceError):
    """权限不足（HTTP 401/403）。禁止重试。"""


class NotFoundError(ServiceError):
    """资源不存在（HTTP 404）。禁止重试。"""


#: 明确禁止重试的错误类别。
NON_RETRYABLE_ERRORS = frozenset(
    {ValidationError, PermissionDeniedError, NotFoundError}
)
