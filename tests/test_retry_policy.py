"""RetryPolicy 参数校验与 execute 语义的单元测试。"""
import unittest

from errors import (
    NON_RETRYABLE_ERRORS,
    PermissionDeniedError,
    RateLimitError,
    ServiceError,
    TransientError,
    ValidationError,
)
from retry_policy import RetryPolicy, execute
from tests.fakes import run_call_site


class TestPolicyValidation(unittest.TestCase):
    """构造时校验：非法参数一律 ValueError。"""

    def test_valid_defaults(self):
        policy = RetryPolicy()
        self.assertEqual(policy.max_attempts, 3)
        self.assertEqual(policy.retryable_errors, (TransientError,))

    def test_invalid_max_attempts(self):
        for bad in (0, -1, 1.5, "3", True, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                RetryPolicy(max_attempts=bad)

    def test_invalid_base_delay(self):
        for bad in (-0.1, "x", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                RetryPolicy(base_delay=bad)

    def test_invalid_backoff_multiplier(self):
        for bad in (0, 0.5, -2, "x"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                RetryPolicy(backoff_multiplier=bad)

    def test_invalid_max_delay(self):
        for bad in (-1, "x"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                RetryPolicy(max_delay=bad)

    def test_invalid_jitter(self):
        for bad in (-0.1, 1.1, 2, "x"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                RetryPolicy(jitter=bad)

    def test_invalid_timeout_budget(self):
        for bad in (0, -5, "x"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                RetryPolicy(timeout_budget=bad)

    def test_invalid_retryable_errors_shape(self):
        for bad in ((), [], [TransientError], (int,), ("x",), (None,)):
            with self.assertRaises(ValueError, msg=repr(bad)):
                RetryPolicy(retryable_errors=bad)

    def test_non_retryable_categories_forbidden(self):
        for exc in NON_RETRYABLE_ERRORS:
            with self.assertRaises(ValueError, msg=exc.__name__):
                RetryPolicy(retryable_errors=(TransientError, exc))

    def test_broad_exception_types_forbidden(self):
        # Exception/ServiceError 会覆盖禁止重试的子类，同样拒绝
        for exc in (Exception, BaseException, ServiceError):
            with self.assertRaises(ValueError, msg=exc.__name__):
                RetryPolicy(retryable_errors=(exc,))


class TestExecute(unittest.TestCase):
    def _run(self, policy, outcomes):
        return run_call_site(lambda t: execute(policy, t), (), outcomes)

    def test_success_first_try(self):
        result = self._run(RetryPolicy(), ["ok"])
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.sleeps, [])
        self.assertEqual(result.outcome, ("ok", "ok"))

    def test_backoff_sequence(self):
        policy = RetryPolicy(
            max_attempts=4, base_delay=1.0, backoff_multiplier=2.0
        )
        result = self._run(policy, [TransientError()] * 3 + ["ok"])
        self.assertEqual(result.attempts, 4)
        self.assertEqual(result.sleeps, [1.0, 2.0, 4.0])

    def test_max_delay_caps_wait(self):
        policy = RetryPolicy(
            max_attempts=4,
            base_delay=1.0,
            backoff_multiplier=3.0,
            max_delay=2.0,
        )
        result = self._run(policy, [TransientError()] * 3 + ["ok"])
        self.assertEqual(result.sleeps, [1.0, 2.0, 2.0])

    def test_exhaustion_raises_original_error(self):
        policy = RetryPolicy(max_attempts=3, base_delay=0.1)
        result = self._run(policy, [TransientError()] * 10)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.sleeps, [0.1, 0.2])
        self.assertEqual(result.outcome, ("error", "TransientError"))

    def test_non_retryable_fails_fast(self):
        policy = RetryPolicy(max_attempts=5, base_delay=0.1)
        result = self._run(policy, [ValidationError("bad")] * 10)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.sleeps, [])
        self.assertEqual(result.outcome, ("error", "ValidationError"))

    def test_only_declared_errors_retried(self):
        policy = RetryPolicy(
            max_attempts=3,
            base_delay=0.1,
            retryable_errors=(RateLimitError,),
        )
        result = self._run(policy, [TransientError()] * 10)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.outcome, ("error", "TransientError"))

    def test_timeout_budget_stops_retrying(self):
        policy = RetryPolicy(
            max_attempts=100,
            base_delay=1.0,
            backoff_multiplier=2.0,
            timeout_budget=3.0,
        )
        result = self._run(policy, [TransientError()] * 100)
        # 等待序列 1, 2, 4：睡完 1+2 后 now=3，3 > 3 不成立，继续睡 4；
        # now=7，下一次检查 7 > 3 成立，抛出。
        self.assertEqual(result.sleeps, [1.0, 2.0, 4.0])
        self.assertEqual(result.attempts, 4)
        self.assertEqual(result.outcome, ("error", "TransientError"))

    def test_jitter_uses_injected_uniform(self):
        policy = RetryPolicy(
            max_attempts=3,
            base_delay=2.0,
            backoff_multiplier=1.0,
            jitter=0.5,
        )
        result = self._run(policy, [TransientError()] * 2 + ["ok"])
        # 确定性 uniform 因子依次为 0.0、1.0：
        # wait1 = 2.0 * (0.5 + 1.0 * 0.0) = 1.0
        # wait2 = 2.0 * (0.5 + 1.0 * 1.0) = 3.0
        self.assertEqual(result.sleeps, [1.0, 3.0])

    def test_permission_denied_never_retried(self):
        policy = RetryPolicy(max_attempts=5, base_delay=0.1)
        result = self._run(policy, [PermissionDeniedError()] * 10)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.outcome, ("error", "PermissionDeniedError"))


if __name__ == "__main__":
    unittest.main()
