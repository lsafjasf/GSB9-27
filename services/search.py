"""搜索服务：声明式重试策略。"""
from errors import TransientError
from retry_policy import RetryPolicy, execute

_QUERY_POLICY = RetryPolicy(
    max_attempts=2,
    base_delay=0.3,
    backoff_multiplier=2.0,
    max_delay=1,
    retryable_errors=(TransientError,),
)


def query(text, transport):
    return execute(
        _QUERY_POLICY, lambda: transport("GET", "/search", {"q": text})
    )
