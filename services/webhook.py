"""Webhook 投递：声明式重试策略。"""
from errors import RateLimitError, TransientError
from retry_policy import RetryPolicy, execute

_DELIVER_POLICY = RetryPolicy(
    max_attempts=6,
    base_delay=0.4,
    backoff_multiplier=2.0,
    max_delay=20,
    jitter=0.5,
    retryable_errors=(TransientError, RateLimitError),
)


def deliver(hook_id, payload, transport):
    return execute(
        _DELIVER_POLICY,
        lambda: transport("POST", "/hooks/%s/deliver" % hook_id, payload),
    )
