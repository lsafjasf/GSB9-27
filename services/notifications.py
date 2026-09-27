"""通知服务：声明式重试策略。

修正：遗留代码捕获 Exception，校验/权限错误也被重试；
现仅重试 TransientError 与 RateLimitError。
"""
from errors import RateLimitError, TransientError
from retry_policy import RetryPolicy, execute

_SEND_EMAIL_POLICY = RetryPolicy(
    max_attempts=5,
    base_delay=0.1,
    backoff_multiplier=2.0,
    max_delay=2,
    retryable_errors=(TransientError, RateLimitError),
)


def send_email(address, subject, transport):
    return execute(
        _SEND_EMAIL_POLICY,
        lambda: transport(
            "POST", "/email/send", {"to": address, "subject": subject}
        ),
    )
