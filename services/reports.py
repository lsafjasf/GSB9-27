"""报表服务：声明式重试策略。"""
from errors import TransientError
from retry_policy import RetryPolicy, execute

_EXPORT_REPORT_POLICY = RetryPolicy(
    max_attempts=5,
    base_delay=1.5,
    backoff_multiplier=2.0,
    max_delay=20,
    jitter=0.5,
    timeout_budget=90,
    retryable_errors=(TransientError,),
)


def export_report(report_id, transport):
    return execute(
        _EXPORT_REPORT_POLICY,
        lambda: transport("GET", "/reports/%s/export" % report_id),
    )
