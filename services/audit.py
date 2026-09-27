"""审计日志：声明式重试策略。

修正：遗留代码捕获 Exception，权限/校验错误也被重试；
现仅重试 TransientError 与 RateLimitError。
"""
from errors import RateLimitError, TransientError
from retry_policy import RetryPolicy, execute

_LOG_EVENT_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=0.15,
    backoff_multiplier=2.0,
    max_delay=2,
    retryable_errors=(TransientError, RateLimitError),
)


def log_event(event, transport):
    return execute(
        _LOG_EVENT_POLICY, lambda: transport("POST", "/audit/events", event)
    )
