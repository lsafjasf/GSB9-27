"""用户同步：声明式重试策略。"""
from errors import RateLimitError, TransientError
from retry_policy import RetryPolicy, execute

_SYNC_USER_POLICY = RetryPolicy(
    max_attempts=8,
    base_delay=1.0,
    backoff_multiplier=2.0,
    max_delay=8,
    timeout_budget=20,
    retryable_errors=(TransientError, RateLimitError),
)


def sync_user(user_id, transport):
    return execute(
        _SYNC_USER_POLICY,
        lambda: transport("POST", "/users/%s/sync" % user_id),
    )
