"""地理定位服务：声明式重试策略（固定间隔，仅重试限流）。"""
from errors import RateLimitError
from retry_policy import RetryPolicy, execute

_LOCATE_IP_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=0.4,
    backoff_multiplier=1.0,
    retryable_errors=(RateLimitError,),
)


def locate_ip(ip, transport):
    return execute(
        _LOCATE_IP_POLICY, lambda: transport("GET", "/geo/%s" % ip)
    )
