"""分级健康检查库（仅标准库）。

检查项分两级：
- REQUIRED（必要）：不通过则整体不可用（DOWN）。
- OPTIONAL（可选）：不通过只导致整体降级（DEGRADED），服务仍可用。

单项结果分三态：
- PASS    通过
- FAIL    未通过（返回 False / 非法返回值 / 抛异常）
- TIMEOUT 超时（单独标注，判定上按不通过处理）

整体状态分三态：
- UP       可用：必要项全部通过，可选项也全部通过
- DEGRADED 降级：必要项全部通过，但至少一个可选项未通过或超时
- DOWN     不可用：至少一个必要项未通过或超时
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional, Tuple, Union

__all__ = [
    "Level",
    "CheckStatus",
    "OverallStatus",
    "CheckItem",
    "CheckResult",
    "HealthReport",
    "HealthChecker",
    "aggregate",
    "render_report",
]


class Level(Enum):
    """检查项级别。"""

    REQUIRED = "required"
    OPTIONAL = "optional"


class CheckStatus(Enum):
    """单项检查结果。"""

    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"


class OverallStatus(Enum):
    """整体健康状态。"""

    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


CheckFn = Callable[[], Union[bool, Tuple[bool, str]]]


@dataclass(frozen=True)
class CheckItem:
    """一个已注册的检查项。"""

    name: str
    fn: CheckFn
    level: Level
    timeout: float


@dataclass(frozen=True)
class CheckResult:
    """单项检查的执行记录（超时记录即 status=TIMEOUT 的条目）。"""

    name: str
    level: Level
    status: CheckStatus
    detail: str
    elapsed: float
    timeout: float


@dataclass
class HealthReport:
    """一次健康检查的完整报告。"""

    overall: OverallStatus
    results: List[CheckResult] = field(default_factory=list)
    started_at: float = 0.0
    elapsed: float = 0.0

    @property
    def failures(self) -> List[CheckResult]:
        return [r for r in self.results if r.status is CheckStatus.FAIL]

    @property
    def timeouts(self) -> List[CheckResult]:
        return [r for r in self.results if r.status is CheckStatus.TIMEOUT]

    def to_dict(self) -> dict:
        return {
            "overall": self.overall.value,
            "elapsed": round(self.elapsed, 6),
            "started_at": self.started_at,
            "checks": [
                {
                    "name": r.name,
                    "level": r.level.value,
                    "status": r.status.value,
                    "detail": r.detail,
                    "elapsed": round(r.elapsed, 6),
                    "timeout": r.timeout,
                }
                for r in self.results
            ],
        }


def aggregate(results: List[CheckResult]) -> OverallStatus:
    """按判定表把单项结果汇总为整体状态（纯函数，便于测试）。

    判定规则：
    - 任一必要项 FAIL/TIMEOUT                  -> DOWN
    - 必要项全部 PASS，任一可选项 FAIL/TIMEOUT -> DEGRADED
    - 全部 PASS（含没有任何检查项的边界情形）  -> UP
    """

    required = [r for r in results if r.level is Level.REQUIRED]
    optional = [r for r in results if r.level is Level.OPTIONAL]
    if any(r.status is not CheckStatus.PASS for r in required):
        return OverallStatus.DOWN
    if any(r.status is not CheckStatus.PASS for r in optional):
        return OverallStatus.DEGRADED
    return OverallStatus.UP


def _normalize_return(value: Union[bool, Tuple[bool, str]]) -> Tuple[bool, str]:
    """把检查函数的返回值归一化为 (是否通过, 详情)。

    约定：返回 True / (True, detail) 为通过；返回 False / (False, detail) 为
    不通过；其余返回值视为违反约定，同样按不通过处理并标注原因。
    """

    if value is True:
        return True, ""
    if value is False:
        return False, "check returned False"
    if (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], bool)
        and isinstance(value[1], str)
    ):
        return value[0], value[1]
    return False, "invalid return value: %r" % (value,)


class HealthChecker:
    """健康检查器：注册检查项，并发执行并汇总整体状态。"""

    def __init__(self, default_timeout: float = 2.0, max_workers: Optional[int] = None):
        if default_timeout <= 0:
            raise ValueError("default_timeout must be > 0")
        self.default_timeout = float(default_timeout)
        self.max_workers = max_workers
        self._items: List[CheckItem] = []

    def add(
        self,
        name: str,
        fn: CheckFn,
        level: Level = Level.REQUIRED,
        timeout: Optional[float] = None,
    ) -> "HealthChecker":
        """注册一个检查项。timeout 为 None 时使用构造时的默认超时。"""

        if not name:
            raise ValueError("check name must be non-empty")
        if any(item.name == name for item in self._items):
            raise ValueError("duplicate check name: %r" % name)
        if not callable(fn):
            raise TypeError("check fn must be callable")
        if not isinstance(level, Level):
            raise TypeError("level must be a Level enum member")
        effective = self.default_timeout if timeout is None else float(timeout)
        if effective <= 0:
            raise ValueError("timeout must be > 0")
        self._items.append(CheckItem(name=name, fn=fn, level=level, timeout=effective))
        return self

    def run(self) -> HealthReport:
        """并发执行全部检查项并返回报告。

        超时、异常、非法返回值都不会中断其他检查项；超时的检查在报告中
        以 TIMEOUT 单独标注，判定上按不通过处理。
        """

        started_at = time.time()
        results: List[CheckResult] = []
        if self._items:
            workers = self.max_workers or min(32, len(self._items))
            executor = ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="healthcheck"
            )
            try:
                futures = {item: executor.submit(item.fn) for item in self._items}
                for item in self._items:
                    results.append(self._await(item, futures[item]))
            finally:
                executor.shutdown(wait=False)
        elapsed = time.time() - started_at
        return HealthReport(
            overall=aggregate(results),
            results=results,
            started_at=started_at,
            elapsed=elapsed,
        )

    @staticmethod
    def _await(item: CheckItem, future) -> CheckResult:
        start = time.monotonic()
        try:
            value = future.result(timeout=item.timeout)
        except FutureTimeoutError:
            return CheckResult(
                name=item.name,
                level=item.level,
                status=CheckStatus.TIMEOUT,
                detail="exceeded timeout %.3fs" % item.timeout,
                elapsed=time.monotonic() - start,
                timeout=item.timeout,
            )
        except Exception as exc:
            return CheckResult(
                name=item.name,
                level=item.level,
                status=CheckStatus.FAIL,
                detail="exception %s: %s" % (type(exc).__name__, exc),
                elapsed=time.monotonic() - start,
                timeout=item.timeout,
            )
        ok, detail = _normalize_return(value)
        return CheckResult(
            name=item.name,
            level=item.level,
            status=CheckStatus.PASS if ok else CheckStatus.FAIL,
            detail=detail,
            elapsed=time.monotonic() - start,
            timeout=item.timeout,
        )


def _format_result_line(result: CheckResult) -> str:
    line = "  [%s] %-24s %-7s %6.3fs" % (
        result.level.value,
        result.name,
        result.status.value,
        result.elapsed,
    )
    if result.detail:
        line += "  %s" % result.detail
    return line


def render_report(report: HealthReport) -> str:
    """把报告渲染为人类可读文本，末尾单独列出超时记录。"""

    lines = [
        "overall: %s  (elapsed %.3fs, %d checks)"
        % (report.overall.value.upper(), report.elapsed, len(report.results))
    ]
    lines.extend(_format_result_line(r) for r in report.results)
    if report.timeouts:
        lines.append("timeout records:")
        for r in report.timeouts:
            lines.append(
                "  - %s (%s): exceeded %.3fs, actual %.3fs"
                % (r.name, r.level.value, r.timeout, r.elapsed)
            )
    return "\n".join(lines)
