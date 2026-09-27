"""会话服务：声明式重试策略。"""
from errors import TransientError
from retry_policy import RetryPolicy, execute

_REFRESH_TOKEN_POLICY = RetryPolicy(
    max_attempts=2,
    base_delay=0.25,
    backoff_multiplier=2.0,
    max_delay=1,
    retryable_errors=(TransientError,),
)


def refresh_token(session_id, transport):
    return execute(
        _REFRESH_TOKEN_POLICY,
        lambda: transport("POST", "/sessions/%s/refresh" % session_id),
    )
