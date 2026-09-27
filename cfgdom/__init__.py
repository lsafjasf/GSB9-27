"""cfgdom：三地址码 → 控制流图 → 支配树 / 支配边界。"""

from .cfg import CFG, BasicBlock, build_cfg
from .dom import (
    dominators,
    immediate_dominators,
    dominator_tree,
    dominance_frontier,
)
from .brute import dom_brute, df_brute

__all__ = [
    "CFG",
    "BasicBlock",
    "build_cfg",
    "dominators",
    "immediate_dominators",
    "dominator_tree",
    "dominance_frontier",
    "dom_brute",
    "df_brute",
]
