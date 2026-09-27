"""用户同步：声明式重试策略。"""
from errors import RateLimitError, TransientError
from retry_policy import RetryPolicy, execute

_SYNC_USER_POLICY = RetryPolicy(
    max_attempts=10,
    base_delay=0.5,
    backoff_multiplier=2.0,
    max_delay=10,
    timeout_budget=30,
    retryable_errors=(TransientError, RateLimitError),
)


def sync_user(user_id, transport):
    return execute(
        _SYNC_USER_POLICY,
        lambda: transport("POST", "/users/%s/sync" % user_id),
    )
