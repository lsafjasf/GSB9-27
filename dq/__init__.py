"""dq — 入库前数据质量检查库（仅依赖 Python 标准库）。

支持五类检查：完整性、唯一性、取值范围、类型一致性、分布漂移。
规则以声明式 JSON 配置，可任意组合；每条命中均带字段、规则、
命中样本与命中比例，便于在入库前定位问题数据。
"""
from .core import CheckResult, Report
from .engine import Engine, run_checks
from .drift import build_baseline, load_baseline, psi

__all__ = [
    "CheckResult",
    "Report",
    "Engine",
    "run_checks",
    "build_baseline",
    "load_baseline",
    "psi",
]

__version__ = "0.1.0"
