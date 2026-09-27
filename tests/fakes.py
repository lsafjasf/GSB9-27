"""差分测试共用设施：假时钟、脚本化 transport、确定性抖动。"""
import itertools
import random
import time
from unittest import mock

from errors import TransientError


class FakeClock:
    """记录每次 sleep 时长，并让 monotonic 随 sleep 前进。"""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self):
        return self.now


class ScriptedTransport:
    """按脚本依次返回结果或抛出异常；脚本用尽后持续抛 TransientError。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else TransientError(
            "script exhausted"
        )
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class RunResult:
    def __init__(self, attempts, sleeps, outcome):
        self.attempts = attempts
        self.sleeps = sleeps
        self.outcome = outcome

    def __eq__(self, other):
        return (
            self.attempts == other.attempts
            and self.sleeps == other.sleeps
            and self.outcome == other.outcome
        )

    def __repr__(self):
        return "RunResult(attempts=%r, sleeps=%r, outcome=%r)" % (
            self.attempts,
            self.sleeps,
            self.outcome,
        )


def run_call_site(fn, args, outcomes):
    """在假时钟与确定性抖动下运行一个调用点，返回可对比的 RunResult。"""
    clock = FakeClock()
    transport = ScriptedTransport(outcomes)
    factors = itertools.cycle([0.0, 1.0, 0.5, 0.25, 0.75])

    def fake_uniform(a, b):
        return a + (b - a) * next(factors)

    with mock.patch.object(time, "sleep", clock.sleep), mock.patch.object(
        time, "monotonic", clock.monotonic
    ), mock.patch.object(random, "uniform", fake_uniform):
        try:
            value = fn(*args, transport)
            outcome = ("ok", value)
        except Exception as exc:
            outcome = ("error", type(exc).__name__)
    return RunResult(transport.calls, clock.sleeps, outcome)
