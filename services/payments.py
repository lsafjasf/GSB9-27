"""支付服务：声明式重试策略。"""
from errors import RateLimitError, TransientError
from retry_policy import RetryPolicy, execute

_GET_BALANCE_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=0.5,
    backoff_multiplier=2.0,
    retryable_errors=(TransientError,),
)

_CHARGE_POLICY = RetryPolicy(
    max_attempts=5,
    base_delay=0.8,
    backoff_multiplier=2.0,
    max_delay=8,
    retryable_errors=(TransientError, RateLimitError),
)


def get_balance(account_id, transport):
    return execute(
        _GET_BALANCE_POLICY,
        lambda: transport("GET", "/accounts/%s/balance" % account_id),
    )


def charge(order_id, amount, transport):
    return execute(
        _CHARGE_POLICY,
        lambda: transport(
            "POST", "/orders/%s/charge" % order_id, {"amount": amount}
        ),
    )
