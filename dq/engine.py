"""检查引擎：单次流式扫描执行组合规则，产出整体报告。"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from .checks import build_check
from .core import Report


class Engine:
    """按声明式规则配置组合多个检查，对数据做单次流式扫描。"""

    def __init__(self, rules_config: Dict[str, Any], base_dir: str = ".",
                 now: Optional[datetime] = None) -> None:
        rules: List[Dict[str, Any]] = rules_config.get("rules", [])
        if not rules:
            raise ValueError("规则配置为空：rules 列表至少需要一条规则")
        self.checks = [build_check(rule, base_dir=base_dir, now=now) for rule in rules]

    def run(self, rows: Iterable[Dict[str, Any]]) -> Report:
        total = 0
        for row in rows:
            for check in self.checks:
                start = time.perf_counter()
                check.update(total, row)
                check._elapsed = getattr(check, "_elapsed", 0.0) + time.perf_counter() - start
            total += 1
        results = []
        for check in self.checks:
            result = check.finalize(total)
            result.elapsed_ms = getattr(check, "_elapsed", 0.0) * 1000.0
            results.append(result)
        return Report(results=results, total_rows=total)


def run_checks(rows: Iterable[Dict[str, Any]], rules_config: Dict[str, Any],
               base_dir: str = ".", now: Optional[datetime] = None) -> Report:
    """便捷入口：构造引擎并运行。"""
    return Engine(rules_config, base_dir=base_dir, now=now).run(rows)
