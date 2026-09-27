"""可配置的重试策略：所有调用点共享的唯一实现。

调用点只声明参数（次数、退避、抖动、超时预算、可重试错误分类），
不再各自手写重试循环。
"""
import random
import time
from dataclasses import dataclass, field

from errors import NON_RETRYABLE_ERRORS, TransientError


@dataclass(frozen=True)
class RetryPolicy:
    """一次远程调用的重试策略。

    max_attempts:       最大尝试次数（含首次），>= 1
    base_delay:         首次重试前的基准等待秒数，>= 0
    backoff_multiplier: 每次重试后等待时间的倍率，>= 1
    max_delay:          单次等待上限秒数，None 表示不设上限
    jitter:             抖动幅度，0~1；实际等待 = 等待 * uniform(1-j, 1+j)
    timeout_budget:     总超时预算秒数，None 表示不限；超限后抛出原始异常
    retryable_errors:   可重试的异常类型元组，禁止包含不可重试类别
    """

    max_attempts: int = 3
    base_delay: float = 0.5
    backoff_multiplier: float = 2.0
    max_delay: float = None
    jitter: float = 0.0
    timeout_budget: float = None
    retryable_errors: tuple = field(default=(TransientError,))

    def __post_init__(self):
        if not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("max_attempts 必须是 >= 1 的整数")
        if not (isinstance(self.base_delay, (int, float)) and self.base_delay >= 0):
            raise ValueError("base_delay 必须是 >= 0 的数")
        if not (
            isinstance(self.backoff_multiplier, (int, float))
            and self.backoff_multiplier >= 1
        ):
            raise ValueError("backoff_multiplier 必须 >= 1")
        if self.max_delay is not None and not (
            isinstance(self.max_delay, (int, float)) and self.max_delay >= 0
        ):
            raise ValueError("max_delay 必须是 >= 0 的数或 None")
        if not (isinstance(self.jitter, (int, float)) and 0 <= self.jitter <= 1):
            raise ValueError("jitter 必须在 [0, 1] 区间内")
        if self.timeout_budget is not None and not (
            isinstance(self.timeout_budget, (int, float))
            and self.timeout_budget > 0
        ):
            raise ValueError("timeout_budget 必须是 > 0 的数或 None")
        if (
            not isinstance(self.retryable_errors, tuple)
            or not self.retryable_errors
            or not all(
                isinstance(e, type) and issubclass(e, BaseException)
                for e in self.retryable_errors
            )
        ):
            raise ValueError("retryable_errors 必须是非空的异常类型元组")
        forbidden = NON_RETRYABLE_ERRORS & frozenset(self.retryable_errors)
        if forbidden:
            names = ", ".join(sorted(e.__name__ for e in forbidden))
            raise ValueError("以下错误类别明确禁止重试: %s" % names)


def execute(policy, fn):
    """按策略执行 fn，返回其结果；失败时按策略重试或抛出原始异常。"""
    start = time.monotonic()
    attempts = 0
    delay = policy.base_delay
    while True:
        try:
            return fn()
        except policy.retryable_errors:
            attempts += 1
            if attempts >= policy.max_attempts:
                raise
            if (
                policy.timeout_budget is not None
                and time.monotonic() - start > policy.timeout_budget
            ):
                raise
            wait = delay
            if policy.max_delay is not None:
                wait = min(wait, policy.max_delay)
            if policy.jitter:
                wait = wait * random.uniform(
                    1 - policy.jitter, 1 + policy.jitter
                )
            time.sleep(wait)
            delay *= policy.backoff_multiplier
