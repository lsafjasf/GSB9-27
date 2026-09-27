"""物流服务：声明式重试策略。

修正：遗留代码把 NotFoundError（404）当作可重试；现仅重试 TransientError。
"""
from errors import TransientError
from retry_policy import RetryPolicy, execute

_CREATE_LABEL_POLICY = RetryPolicy(
    max_attempts=4,
    base_delay=0.6,
    backoff_multiplier=2.0,
    max_delay=8,
    retryable_errors=(TransientError,),
)


def create_label(order_id, transport):
    return execute(
        _CREATE_LABEL_POLICY,
        lambda: transport("POST", "/orders/%s/label" % order_id),
    )
