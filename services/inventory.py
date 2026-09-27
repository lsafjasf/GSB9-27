"""库存服务：声明式重试策略。"""
from errors import TransientError
from retry_policy import RetryPolicy, execute

_RESERVE_POLICY = RetryPolicy(
    max_attempts=4,
    base_delay=0.25,
    backoff_multiplier=3.0,
    max_delay=6,
    jitter=0.5,
    retryable_errors=(TransientError,),
)


def reserve(sku, quantity, transport):
    return execute(
        _RESERVE_POLICY,
        lambda: transport(
            "POST", "/inventory/%s/reserve" % sku, {"quantity": quantity}
        ),
    )
