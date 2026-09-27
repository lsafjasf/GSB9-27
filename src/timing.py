"""重构后的时间处理：所有时间读取都通过注入的 Clock。

与 legacy_timing.py 分支结构一一对应，差别只在时间来源：

- is_expired 用墙上时间（过期时间是日历时刻）。
- remaining / retry 用单调时间（间隔测量，不受回拨影响）。

生产环境注入 SystemClock，测试注入 FakeClock，逻辑本身不接触任何
全局状态，因此完全确定、可重复，且不需要真实等待。
"""

from .clocks import Clock, SystemClock, FakeClock  # noqa: F401  重导出方便使用


class DeadlineExpired(Exception):
    """重试在总截止时间内未能成功。"""


def is_expired(expires_at, clock):
    """绝对过期判定（日历时刻语义，用墙上时间）。"""
    return clock.wall() >= expires_at


def remaining(started_at, duration, clock):
    """单调时间剩余，下限截断为 0（用单调时间，不受回拨影响）。"""
    elapsed = clock.monotonic() - started_at
    left = duration - elapsed
    if left < 0.0:
        return 0.0
    return left


def retry(operation, clock, max_attempts=3, base_delay=0.1,
          max_delay=1.0, timeout=5.0):
    """指数退避重试（语义同 legacy_timing.retry，时间来源可注入）。

    - 成功：返回 operation() 的结果。
    - 达到 max_attempts：抛出最后一次异常。
    - 下一次退避后将超过 timeout：抛出 DeadlineExpired。
    """
    start = clock.monotonic()
    delay = base_delay
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == max_attempts:
                raise
            elapsed = clock.monotonic() - start
            if elapsed + delay > timeout:
                raise DeadlineExpired(
                    "attempt %d: %0.3fs elapsed, next backoff %0.3fs "
                    "exceeds timeout %0.3fs"
                    % (attempt, elapsed, delay, timeout)
                ) from exc
            clock.sleep(delay)
            delay = min(delay * 2, max_delay)
    raise last_exc  # pragma: no cover
