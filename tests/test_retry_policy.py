"""RetryPolicy 参数校验与 execute 行为的单元测试。"""
import unittest

from errors import (
    NON_RETRYABLE_ERRORS,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    TransientError,
    ValidationError,
)
from retry_policy import RetryPolicy, execute
from tests.fakes import run_call_site


class TestPolicyValidation(unittest.TestCase):
    def test_defaults_are_valid(self):
        RetryPolicy()

    def test_max_attempts_must_be_positive_int(self):
        for bad in (0, -1, 1.5, "3"):
            with self.assertRaises(ValueError):
                RetryPolicy(max_attempts=bad)

    def test_base_delay_must_be_non_negative(self):
        with self.assertRaises(ValueError):
            RetryPolicy(base_delay=-0.1)

    def test_backoff_multiplier_must_be_at_least_one(self):
        with self.assertRaises(ValueError):
            RetryPolicy(backoff_multiplier=0.5)

    def test_max_delay_must_be_non_negative_or_none(self):
        RetryPolicy(max_delay=None)
        RetryPolicy(max_delay=0)
        with self.assertRaises(ValueError):
            RetryPolicy(max_delay=-1)

    def test_jitter_must_be_within_unit_interval(self):
        RetryPolicy(jitter=0)
        RetryPolicy(jitter=1)
        for bad in (-0.1, 1.1):
            with self.assertRaises(ValueError):
                RetryPolicy(jitter=bad)

    def test_timeout_budget_must_be_positive_or_none(self):
        RetryPolicy(timeout_budget=None)
        RetryPolicy(timeout_budget=5)
        for bad in (0, -5):
            with self.assertRaises(ValueError):
                RetryPolicy(timeout_budget=bad)

    def test_retryable_errors_must_be_non_empty_exception_tuple(self):
        for bad in ((), [], [TransientError], (int,), (TransientError, 42)):
            with self.assertRaises(ValueError):
                RetryPolicy(retryable_errors=bad)

    def test_non_retryable_categories_are_forbidden(self):
        for cls in sorted(NON_RETRYABLE_ERRORS, key=lambda c: c.__name__):
            with self.assertRaises(ValueError):
                RetryPolicy(retryable_errors=(TransientError, cls))
            with self.assertRaises(ValueError):
                RetryPolicy(retryable_errors=(cls,))


class TestExecute(unittest.TestCase):
    def test_success_without_retry(self):
        policy = RetryPolicy(max_attempts=3)
        result = run_call_site(
            lambda t: execute(policy, lambda: t("GET", "/x")), (), ["ok"]
        )
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.sleeps, [])
        self.assertEqual(result.outcome, ("ok", "ok"))

    def test_retries_then_succeeds(self):
        policy = RetryPolicy(max_attempts=3, base_delay=0.5)
        result = run_call_site(
            lambda t: execute(policy, lambda: t("GET", "/x")),
            (),
            [TransientError(), TransientError(), "ok"],
        )
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.sleeps, [0.5, 1.0])
        self.assertEqual(result.outcome, ("ok", "ok"))

    def test_gives_up_after_max_attempts(self):
        policy = RetryPolicy(max_attempts=3, base_delay=0.5)
        result = run_call_site(
            lambda t: execute(policy, lambda: t("GET", "/x")),
            (),
            [TransientError()] * 10,
        )
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.sleeps, [0.5, 1.0])
        self.assertEqual(result.outcome, ("error", "TransientError"))

    def test_non_retryable_error_fails_fast(self):
        policy = RetryPolicy(
            max_attempts=5, retryable_errors=(TransientError, RateLimitError)
        )
        for exc in (
            ValidationError(),
            PermissionDeniedError(),
            NotFoundError(),
        ):
            result = run_call_site(
                lambda t: execute(policy, lambda: t("GET", "/x")), (), [exc]
            )
            self.assertEqual(result.attempts, 1)
            self.assertEqual(result.sleeps, [])
            self.assertEqual(result.outcome, ("error", type(exc).__name__))

    def test_timeout_budget_stops_retrying(self):
        policy = RetryPolicy(
            max_attempts=100, base_delay=4.0, backoff_multiplier=1.0,
            timeout_budget=10,
        )
        result = run_call_site(
            lambda t: execute(policy, lambda: t("GET", "/x")),
            (),
            [TransientError()] * 100,
        )
        # 等待 4、4 后累计 8 <= 10 继续；再等到 12 > 10 停止
        self.assertEqual(result.attempts, 4)
        self.assertEqual(result.sleeps, [4.0, 4.0, 4.0])
        self.assertEqual(result.outcome, ("error", "TransientError"))


if __name__ == "__main__":
    unittest.main()
