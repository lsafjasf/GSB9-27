"""重构前的时间处理（保留作为差分测试基准）。

问题：超时、过期、重试逻辑直接读取系统时间（time.time / time.sleep），
测试只能真实等待，且 time.time() 在系统时间回拨时可能倒退或跳变，
导致截止时间判定不可预测。

这里的函数分支结构与 timing.py 严格对应；差分测试保证两者在同一
时刻序列下判定结果一致。
"""

import time


class DeadlineExpired(Exception):
    """重试在总截止时间内未能成功。"""


def is_expired(expires_at):
    """绝对过期判定（日历时刻语义）。"""
    return time.time() >= expires_at


def remaining(deadline_started_at, duration):
    """剩余时间，下限截断为 0。"""
    elapsed = time.time() - deadline_started_at
    left = duration - elapsed
    if left < 0.0:
        return 0.0
    return left


def retry(operation, max_attempts=3, base_delay=0.1, max_delay=1.0, timeout=5.0):
    """指数退避重试。

    - 成功：返回 operation() 的结果。
    - 达到 max_attempts：抛出最后一次异常。
    - 下一次退避后将超过 timeout：抛出 DeadlineExpired。
    """
    start = time.time()
    delay = base_delay
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001 - 重试策略需要捕获全部异常
            last_exc = exc
            if attempt == max_attempts:
                raise
            if time.time() - start + delay > timeout:
                raise DeadlineExpired(
                    "attempt %d: %0.3fs elapsed, next backoff %0.3fs "
                    "exceeds timeout %0.3fs"
                    % (attempt, time.time() - start, delay, timeout)
                ) from exc
            time.sleep(delay)
            delay = min(delay * 2, max_delay)
    raise last_exc  # pragma: no cover - 循环内必然 return/raise
