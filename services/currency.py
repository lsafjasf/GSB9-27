"""汇率服务：声明式重试策略。"""
from errors import TransientError
from retry_policy import RetryPolicy, execute

_GET_RATE_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=0.3,
    backoff_multiplier=2.0,
    max_delay=5,
    retryable_errors=(TransientError,),
)


def get_rate(pair, transport):
    return execute(
        _GET_RATE_POLICY, lambda: transport("GET", "/fx/%s" % pair)
    )
