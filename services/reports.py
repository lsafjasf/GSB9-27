"""报表服务：声明式重试策略。"""
from errors import TransientError
from retry_policy import RetryPolicy, execute

_EXPORT_POLICY = RetryPolicy(
    max_attempts=4,
    base_delay=2.0,
    backoff_multiplier=2.0,
    max_delay=30,
    jitter=0.5,
    timeout_budget=60,
    retryable_errors=(TransientError,),
)


def export_report(report_id, transport):
    return execute(
        _EXPORT_POLICY,
        lambda: transport("GET", "/reports/%s/export" % report_id),
    )
