"""稳态分布计算包：幂迭代 + 线性方程组两条求解路径。"""

from .markov import (
    InvalidChainError,
    NonConvergenceError,
    NonUniqueStationaryError,
    Structure,
    PowerResult,
    LinearResult,
    StationaryResult,
    analyze_structure,
    power_iteration,
    stationary_linear,
    steady_state,
    stationary_residual,
    is_probability_distribution,
)

__all__ = [
    "InvalidChainError",
    "NonConvergenceError",
    "NonUniqueStationaryError",
    "Structure",
    "PowerResult",
    "LinearResult",
    "StationaryResult",
    "analyze_structure",
    "power_iteration",
    "stationary_linear",
    "stationary_residual",
    "is_probability_distribution",
    "steady_state",
]
