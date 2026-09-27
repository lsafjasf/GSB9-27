"""可配置的重试策略：全部调用点共享的唯一实现。

调用点只声明参数（次数、退避、抖动、超时预算、可重试错误分类），
不再各自手写重试循环。策略在构造时校验参数合法性，
并拒绝把禁止重试的错误类别声明为可重试。
"""
import random
import time
from dataclasses import dataclass, field

from errors import NON_RETRYABLE_ERRORS, TransientError


@dataclass(frozen=True)
class RetryPolicy:
    """一次远程调用的重试策略。

    max_attempts:       最大尝试次数（含首次），>= 1 的整数
    base_delay:         首次重试前的基准等待秒数，>= 0
    backoff_multiplier: 每次重试后等待时间的倍率，>= 1（1 表示固定间隔）
    max_delay:          单次等待上限秒数，None 表示不设上限
    jitter:             抖动比例，[0, 1]；实际等待 = 等待 * uniform(1-j, 1+j)
    timeout_budget:     总超时预算秒数，None 表示不限；
                        超过预算后抛出原始异常（在下一次等待前检查）
    retryable_errors:   可重试的异常类型元组，非空；
                        不得覆盖 NON_RETRYABLE_ERRORS 中的任何类别
    """

    max_attempts: int = 3
    base_delay: float = 0.5
    backoff_multiplier: float = 2.0
    max_delay: float = None
    jitter: float = 0.0
    timeout_budget: float = None
    retryable_errors: tuple = field(default=(TransientError,))

    def __post_init__(self):
        if isinstance(self.max_attempts, bool) or not isinstance(
            self.max_attempts, int
        ):
            raise ValueError("max_attempts 必须是整数")
        if self.max_attempts < 1:
            raise ValueError("max_attempts 必须 >= 1")
        if not _is_number(self.base_delay) or self.base_delay < 0:
            raise ValueError("base_delay 必须是 >= 0 的数")
        if not _is_number(self.backoff_multiplier):
            raise ValueError("backoff_multiplier 必须是数")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier 必须 >= 1")
        if self.max_delay is not None and (
            not _is_number(self.max_delay) or self.max_delay < 0
        ):
            raise ValueError("max_delay 必须是 >= 0 的数或 None")
        if not _is_number(self.jitter) or not 0 <= self.jitter <= 1:
            raise ValueError("jitter 必须在 [0, 1] 区间内")
        if self.timeout_budget is not None and (
            not _is_number(self.timeout_budget) or self.timeout_budget <= 0
        ):
            raise ValueError("timeout_budget 必须是 > 0 的数或 None")
        if not isinstance(self.retryable_errors, tuple):
            raise ValueError("retryable_errors 必须是异常类型元组")
        if not self.retryable_errors:
            raise ValueError("retryable_errors 不能为空")
        for exc in self.retryable_errors:
            if not (isinstance(exc, type) and issubclass(exc, BaseException)):
                raise ValueError(
                    "retryable_errors 的元素必须是异常类型: %r" % (exc,)
                )
            for forbidden in NON_RETRYABLE_ERRORS:
                if issubclass(forbidden, exc):
                    raise ValueError(
                        "%s 会覆盖禁止重试的 %s，不允许声明为可重试"
                        % (exc.__name__, forbidden.__name__)
                    )


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def execute(policy, fn):
    """按策略执行 fn 并返回其结果；失败时按策略重试或抛出原始异常。"""
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
