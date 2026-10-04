"""healthcheck 库的自测（unittest，仅标准库）。

运行：python3 -m unittest -v
覆盖：判定表全组合、四大情形（全部正常 / 可选依赖不可用 / 必要依赖不可用 /
检查超时）、以及边界用例（空检查、超时边界、并发、非法注册、非法返回值等）。
"""

import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from healthcheck import (
    CheckResult,
    CheckStatus,
    HealthChecker,
    Level,
    OverallStatus,
    aggregate,
    render_report,
)

FAST_TIMEOUT = 0.2
MARGIN = 0.15


def ok():
    return True


def ok_detail():
    return True, "everything fine"


def bad():
    return False


def bad_detail():
    return False, "connection refused"


def boom():
    raise RuntimeError("connection refused")


def slow(seconds):
    def _check():
        time.sleep(seconds)
        return True

    return _check


def make_result(level, status):
    return CheckResult(
        name="probe",
        level=level,
        status=status,
        detail="",
        elapsed=0.0,
        timeout=1.0,
    )


class DecisionTableTest(unittest.TestCase):
    """判定表：必要项结果 x 可选项结果 -> 整体状态，全组合。"""

    CASES = [
        # (必要项结果, 可选项结果, 期望整体状态)
        (CheckStatus.PASS, CheckStatus.PASS, OverallStatus.UP),
        (CheckStatus.PASS, CheckStatus.FAIL, OverallStatus.DEGRADED),
        (CheckStatus.PASS, CheckStatus.TIMEOUT, OverallStatus.DEGRADED),
        (CheckStatus.FAIL, CheckStatus.PASS, OverallStatus.DOWN),
        (CheckStatus.FAIL, CheckStatus.FAIL, OverallStatus.DOWN),
        (CheckStatus.FAIL, CheckStatus.TIMEOUT, OverallStatus.DOWN),
        (CheckStatus.TIMEOUT, CheckStatus.PASS, OverallStatus.DOWN),
        (CheckStatus.TIMEOUT, CheckStatus.FAIL, OverallStatus.DOWN),
        (CheckStatus.TIMEOUT, CheckStatus.TIMEOUT, OverallStatus.DOWN),
    ]

    def test_all_combinations(self):
        for required_status, optional_status, expected in self.CASES:
            results = [
                make_result(Level.REQUIRED, required_status),
                make_result(Level.OPTIONAL, optional_status),
            ]
            with self.subTest(required=required_status, optional=optional_status):
                self.assertIs(aggregate(results), expected)

    def test_empty_checks_is_up(self):
        self.assertIs(aggregate([]), OverallStatus.UP)

    def test_only_optional_passing_is_up(self):
        results = [make_result(Level.OPTIONAL, CheckStatus.PASS)]
        self.assertIs(aggregate(results), OverallStatus.UP)

    def test_only_optional_failing_is_degraded(self):
        results = [make_result(Level.OPTIONAL, CheckStatus.FAIL)]
        self.assertIs(aggregate(results), OverallStatus.DEGRADED)

    def test_only_required_failing_is_down(self):
        results = [make_result(Level.REQUIRED, CheckStatus.FAIL)]
        self.assertIs(aggregate(results), OverallStatus.DOWN)


class ScenarioTest(unittest.TestCase):
    """四大情形：全部正常 / 可选依赖不可用 / 必要依赖不可用 / 检查超时。"""

    def test_all_pass_is_up(self):
        checker = HealthChecker(default_timeout=FAST_TIMEOUT)
        checker.add("db", ok, Level.REQUIRED)
        checker.add("cache", ok_detail, Level.OPTIONAL)
        report = checker.run()
        self.assertIs(report.overall, OverallStatus.UP)
        self.assertEqual(len(report.results), 2)
        self.assertTrue(all(r.status is CheckStatus.PASS for r in report.results))
        self.assertEqual(report.failures, [])
        self.assertEqual(report.timeouts, [])

    def test_optional_dependency_down_is_degraded(self):
        checker = HealthChecker(default_timeout=FAST_TIMEOUT)
        checker.add("db", ok, Level.REQUIRED)
        checker.add("cache", bad_detail, Level.OPTIONAL)
        report = checker.run()
        self.assertIs(report.overall, OverallStatus.DEGRADED)
        cache = report.results[1]
        self.assertIs(cache.status, CheckStatus.FAIL)
        self.assertEqual(cache.detail, "connection refused")

    def test_required_dependency_down_is_down(self):
        checker = HealthChecker(default_timeout=FAST_TIMEOUT)
        checker.add("db", bad, Level.REQUIRED)
        checker.add("cache", ok, Level.OPTIONAL)
        report = checker.run()
        self.assertIs(report.overall, OverallStatus.DOWN)
        self.assertIs(report.results[0].status, CheckStatus.FAIL)
        self.assertIs(report.results[1].status, CheckStatus.PASS)

    def test_optional_timeout_is_degraded_and_recorded(self):
        checker = HealthChecker(default_timeout=FAST_TIMEOUT)
        checker.add("db", ok, Level.REQUIRED)
        checker.add("cache", slow(FAST_TIMEOUT * 5), Level.OPTIONAL)
        report = checker.run()
        self.assertIs(report.overall, OverallStatus.DEGRADED)
        self.assertEqual(len(report.timeouts), 1)
        record = report.timeouts[0]
        self.assertEqual(record.name, "cache")
        self.assertIs(record.status, CheckStatus.TIMEOUT)
        self.assertIn("exceeded timeout", record.detail)
        self.assertGreaterEqual(record.elapsed, FAST_TIMEOUT)
        self.assertLess(record.elapsed, FAST_TIMEOUT * 5)

    def test_required_timeout_is_down_and_recorded(self):
        checker = HealthChecker(default_timeout=FAST_TIMEOUT)
        checker.add("db", slow(FAST_TIMEOUT * 5), Level.REQUIRED)
        checker.add("cache", ok, Level.OPTIONAL)
        report = checker.run()
        self.assertIs(report.overall, OverallStatus.DOWN)
        self.assertEqual([r.name for r in report.timeouts], ["db"])
        self.assertIs(report.results[1].status, CheckStatus.PASS)

    def test_timeout_does_not_block_other_checks(self):
        checker = HealthChecker(default_timeout=FAST_TIMEOUT)
        checker.add("db", slow(FAST_TIMEOUT * 5), Level.REQUIRED)
        checker.add("mq", boom, Level.REQUIRED)
        checker.add("cache", ok, Level.OPTIONAL)
        started = time.monotonic()
        report = checker.run()
        elapsed = time.monotonic() - started
        self.assertIs(report.overall, OverallStatus.DOWN)
        self.assertEqual(len(report.results), 3)
        self.assertLess(elapsed, FAST_TIMEOUT * 5)


class EdgeCaseTest(unittest.TestCase):
    """边界用例。"""

    def test_no_checks_registered_is_up(self):
        report = HealthChecker().run()
        self.assertIs(report.overall, OverallStatus.UP)
        self.assertEqual(report.results, [])

    def test_exception_in_check_is_fail_not_timeout(self):
        checker = HealthChecker(default_timeout=FAST_TIMEOUT)
        checker.add("db", boom, Level.REQUIRED)
        report = checker.run()
        self.assertIs(report.overall, OverallStatus.DOWN)
        result = report.results[0]
        self.assertIs(result.status, CheckStatus.FAIL)
        self.assertIn("RuntimeError", result.detail)
        self.assertIn("connection refused", result.detail)
        self.assertEqual(report.timeouts, [])

    def test_invalid_return_value_is_fail(self):
        checker = HealthChecker(default_timeout=FAST_TIMEOUT)
        checker.add("db", lambda: None, Level.REQUIRED)
        checker.add("cache", lambda: "yes", Level.OPTIONAL)
        report = checker.run()
        self.assertIs(report.overall, OverallStatus.DOWN)
        self.assertIn("invalid return value", report.results[0].detail)
        self.assertIn("invalid return value", report.results[1].detail)

    def test_tuple_return_convention(self):
        checker = HealthChecker(default_timeout=FAST_TIMEOUT)
        checker.add("db", lambda: (True, "p50=2ms"), Level.REQUIRED)
        checker.add("cache", lambda: (False, "stale"), Level.OPTIONAL)
        report = checker.run()
        self.assertIs(report.overall, OverallStatus.DEGRADED)
        self.assertEqual(report.results[0].detail, "p50=2ms")
        self.assertEqual(report.results[1].detail, "stale")

    def test_finish_just_before_timeout_passes(self):
        checker = HealthChecker(default_timeout=FAST_TIMEOUT + MARGIN)
        checker.add("db", slow(FAST_TIMEOUT), Level.REQUIRED)
        report = checker.run()
        self.assertIs(report.overall, OverallStatus.UP)
        self.assertIs(report.results[0].status, CheckStatus.PASS)

    def test_finish_just_after_timeout_times_out(self):
        checker = HealthChecker(default_timeout=FAST_TIMEOUT)
        checker.add("db", slow(FAST_TIMEOUT + MARGIN), Level.REQUIRED)
        report = checker.run()
        self.assertIs(report.overall, OverallStatus.DOWN)
        self.assertIs(report.results[0].status, CheckStatus.TIMEOUT)

    def test_per_check_timeout_overrides_default(self):
        checker = HealthChecker(default_timeout=5.0)
        checker.add("db", slow(0.4), Level.REQUIRED, timeout=0.1)
        report = checker.run()
        self.assertIs(report.results[0].status, CheckStatus.TIMEOUT)
        self.assertEqual(report.results[0].timeout, 0.1)

    def test_checks_run_concurrently(self):
        checker = HealthChecker(default_timeout=2.0)
        for index in range(4):
            checker.add("dep-%d" % index, slow(0.3), Level.OPTIONAL)
        started = time.monotonic()
        report = checker.run()
        elapsed = time.monotonic() - started
        self.assertIs(report.overall, OverallStatus.UP)
        self.assertLess(elapsed, 0.9)

    def test_report_contains_elapsed_and_started_at(self):
        report = HealthChecker().run()
        self.assertGreater(report.started_at, 0)
        self.assertGreaterEqual(report.elapsed, 0.0)

    def test_to_dict_is_json_serializable(self):
        import json

        checker = HealthChecker(default_timeout=FAST_TIMEOUT)
        checker.add("db", ok, Level.REQUIRED)
        checker.add("cache", slow(FAST_TIMEOUT * 5), Level.OPTIONAL)
        payload = checker.run().to_dict()
        encoded = json.dumps(payload)
        self.assertIn('"overall": "degraded"', encoded)
        self.assertEqual(payload["checks"][1]["status"], "timeout")

    def test_render_report_lists_timeout_records(self):
        checker = HealthChecker(default_timeout=FAST_TIMEOUT)
        checker.add("db", ok, Level.REQUIRED)
        checker.add("cache", slow(FAST_TIMEOUT * 5), Level.OPTIONAL)
        text = render_report(checker.run())
        self.assertIn("overall: DEGRADED", text)
        self.assertIn("timeout records:", text)
        self.assertIn("cache (optional)", text)


class RegistrationValidationTest(unittest.TestCase):
    """非法注册参数在注册期就报错。"""

    def test_empty_name_rejected(self):
        with self.assertRaises(ValueError):
            HealthChecker().add("", ok)

    def test_duplicate_name_rejected(self):
        checker = HealthChecker()
        checker.add("db", ok)
        with self.assertRaises(ValueError):
            checker.add("db", ok)

    def test_non_callable_rejected(self):
        with self.assertRaises(TypeError):
            HealthChecker().add("db", "not-a-function")

    def test_invalid_level_rejected(self):
        with self.assertRaises(TypeError):
            HealthChecker().add("db", ok, level="required")

    def test_non_positive_timeout_rejected(self):
        with self.assertRaises(ValueError):
            HealthChecker().add("db", ok, timeout=0)
        with self.assertRaises(ValueError):
            HealthChecker(default_timeout=0)

    def test_add_returns_self_for_chaining(self):
        checker = HealthChecker()
        self.assertIs(checker.add("db", ok), checker)


class DemoCliTest(unittest.TestCase):
    """端到端：demo.py 的四个场景退出码分别为 0/1/2/2。"""

    def test_demo_exit_codes(self):
        demo = Path(__file__).resolve().parent / "demo.py"
        proc = subprocess.run(
            [sys.executable, str(demo)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("overall: UP", proc.stdout)
        self.assertIn("overall: DEGRADED", proc.stdout)
        self.assertIn("overall: DOWN", proc.stdout)
        self.assertIn("timeout records:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
