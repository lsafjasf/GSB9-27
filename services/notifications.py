"""通知服务：声明式重试策略。

修正：遗留代码捕获 Exception，参数非法/权限不足/404 也被重试；
现仅重试 TransientError 与 RateLimitError。
"""
from errors import RateLimitError, TransientError
from retry_policy import RetryPolicy, execute

_SEND_EMAIL_POLICY = RetryPolicy(
    max_attempts=4,
    base_delay=0.2,
    backoff_multiplier=2.0,
    max_delay=3,
    retryable_errors=(TransientError, RateLimitError),
)


def send_email(address, subject, transport):
    return execute(
        _SEND_EMAIL_POLICY,
        lambda: transport(
            "POST", "/email/send", {"to": address, "subject": subject}
        ),
    )
