"""定价服务：声明式重试策略。"""
from errors import TransientError
from retry_policy import RetryPolicy, execute

_GET_QUOTE_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=0.25,
    backoff_multiplier=2.0,
    max_delay=2,
    retryable_errors=(TransientError,),
)


def get_quote(sku, transport):
    return execute(
        _GET_QUOTE_POLICY,
        lambda: transport("GET", "/pricing/%s/quote" % sku),
    )
