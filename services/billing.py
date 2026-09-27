"""账单服务：声明式重试策略。

修正：遗留代码捕获 Exception，校验/权限错误也被重试；
现仅重试 TransientError 与 RateLimitError。
"""
from errors import RateLimitError, TransientError
from retry_policy import RetryPolicy, execute

_CREATE_INVOICE_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=0.5,
    backoff_multiplier=2.0,
    max_delay=4,
    retryable_errors=(TransientError, RateLimitError),
)


def create_invoice(order_id, transport):
    return execute(
        _CREATE_INVOICE_POLICY,
        lambda: transport("POST", "/orders/%s/invoice" % order_id),
    )
