"""ifacecheck：接口兼容检查库（仅标准库）。

- compare : 静态对比两个版本的接口定义，逐项判定并标注影响等级
- runtime : 实际调用测试，并与静态判定对照，列出不一致条目
- report  : 输出 Markdown 判定表 / 对照表
"""
from .compare import (  # noqa: F401
    BREAKING, CAUTION, COMPATIBLE, CompareReport, Judgment, compare_interfaces,
)
from .model import Interface  # noqa: F401
from .runtime import (  # noqa: F401
    CallCase, CaseResult, Inconsistency, check_consistency, load_cases,
    load_impl, run_cases,
)
