"""分级健康检查演示：覆盖四大情形并打印超时记录。

退出码约定（可直接对接探针/脚本）：
    0 = UP       可用
    1 = DEGRADED 降级（可用，但建议告警）
    2 = DOWN     不可用

运行：python3 demo.py
"""

import sys
import time

from healthcheck import HealthChecker, Level, OverallStatus, render_report

EXIT_CODE = {
    OverallStatus.UP: 0,
    OverallStatus.DEGRADED: 1,
    OverallStatus.DOWN: 2,
}


def make_ok(detail=""):
    def _check():
        return True, detail

    return _check


def make_fail(detail):
    def _check():
        return False, detail

    return _check


def make_slow(seconds):
    def _check():
        time.sleep(seconds)
        return True

    return _check


def scenario_all_up():
    """情形 1：全部正常 -> UP。"""
    checker = HealthChecker(default_timeout=1.0)
    checker.add("db", make_ok("p50=2ms"), Level.REQUIRED)
    checker.add("mq", make_ok(), Level.REQUIRED)
    checker.add("cache", make_ok(), Level.OPTIONAL)
    checker.add("metrics", make_ok(), Level.OPTIONAL)
    return checker.run()


def scenario_optional_down():
    """情形 2：可选依赖不可用 -> DEGRADED（服务仍可用，不应被摘除）。"""
    checker = HealthChecker(default_timeout=1.0)
    checker.add("db", make_ok("p50=2ms"), Level.REQUIRED)
    checker.add("mq", make_ok(), Level.REQUIRED)
    checker.add("cache", make_fail("connection refused"), Level.OPTIONAL)
    checker.add("metrics", make_ok(), Level.OPTIONAL)
    return checker.run()


def scenario_required_down():
    """情形 3：必要依赖不可用 -> DOWN（本实例应被摘除）。"""
    checker = HealthChecker(default_timeout=1.0)
    checker.add("db", make_fail("too many connections"), Level.REQUIRED)
    checker.add("mq", make_ok(), Level.REQUIRED)
    checker.add("cache", make_ok(), Level.OPTIONAL)
    return checker.run()


def scenario_timeout():
    """情形 4：检查超时 -> 单独标注，按不通过参与判定。"""
    print("--- 4a. 可选依赖超时 -> DEGRADED ---")
    checker = HealthChecker(default_timeout=1.0)
    checker.add("db", make_ok(), Level.REQUIRED)
    checker.add("cache", make_slow(2.0), Level.OPTIONAL, timeout=0.3)
    report = checker.run()
    print(render_report(report))
    print()

    print("--- 4b. 必要依赖超时 -> DOWN ---")
    checker = HealthChecker(default_timeout=1.0)
    checker.add("db", make_slow(2.0), Level.REQUIRED, timeout=0.3)
    checker.add("cache", make_ok(), Level.OPTIONAL)
    report = checker.run()
    print(render_report(report))
    return report


def main():
    scenarios = [
        ("1. 全部正常 -> UP", scenario_all_up),
        ("2. 可选依赖不可用 -> DEGRADED", scenario_optional_down),
        ("3. 必要依赖不可用 -> DOWN", scenario_required_down),
    ]
    reports = []
    for title, fn in scenarios:
        print("--- %s ---" % title)
        report = fn()
        print(render_report(report))
        print()
        reports.append(report)

    print("4. 检查超时（分可选/必要两种）")
    reports.append(scenario_timeout())

    worst = max(reports, key=lambda r: EXIT_CODE[r.overall])
    code = EXIT_CODE[worst.overall]
    print()
    print("worst overall: %s -> exit code %d" % (worst.overall.value.upper(), code))
    return code


if __name__ == "__main__":
    sys.exit(main())
