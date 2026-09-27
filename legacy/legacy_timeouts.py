"""重构前的旧代码（基线）。

典型问题：到处直接读 ``time.time()`` / ``time.sleep()``，
墙上时间与单调时间不分。超时、退避等待也用墙上时间，
一旦发生 NTP 回拨，超时被延长、重试判定被扭曲。

差分测试中会用 unittest.mock 替换本模块引用的 time 函数，
让"旧代码"和"新代码"在完全相同的时刻序列上逐分支对齐。
"""

import time


def now() -> float:
    return time.time()


def is_token_expired(expires_at, skew_seconds=0.0):
    """旧逻辑：直接拿墙上时间比绝对过期点。"""
    return time.time() + skew_seconds >= expires_at


def make_token_expiry(ttl_seconds):
    return time.time() + ttl_seconds


def token_seconds_remaining(expires_at, skew_seconds=0.0):
    return expires_at - time.time() - skew_seconds


def timeout_deadline(seconds):
    """旧逻辑：deadline 也建在墙上时间上。"""
    return time.time() + seconds


def is_timeout_expired(deadline):
    return time.time() >= deadline


def timeout_remaining(deadline):
    diff = deadline - time.time()
    return diff if diff > 0.0 else 0.0


def retry_delay(attempt, base_delay=0.1, multiplier=2.0, max_delay=5.0):
    raw = base_delay * (multiplier ** (attempt - 1))
    return raw if raw < max_delay else max_delay


def should_retry(attempt, max_attempts, retryable=True):
    return attempt < max_attempts and retryable


def run_with_retry(func, max_attempts=3, base_delay=0.1,
                   multiplier=2.0, max_delay=5.0):
    """旧逻辑：重试、退避、睡眠全部直接依赖系统时间。"""
    attempt = 0
    while True:
        attempt += 1
        try:
            return func()
        except Exception as exc:
            if not should_retry(attempt, max_attempts, retryable=True):
                raise RuntimeError(
                    f"failed after {attempt} attempt(s): {exc!r}")
            time.sleep(retry_delay(attempt, base_delay, multiplier, max_delay))
