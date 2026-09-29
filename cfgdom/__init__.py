"""cfgdom：三地址码 → 控制流图 → 支配树 / 支配边界 → SSA → 解释执行。"""

from .cfg import CFG, BasicBlock, build_cfg
from .dom import (
    dominators,
    immediate_dominators,
    dominator_tree,
    dominance_frontier,
)
from .brute import dom_brute, df_brute
from .ssa import (
    SSAProgram,
    to_ssa,
    to_ssa_cfg,
    compute_defsites,
    place_phis,
    iterated_dominance_frontier,
    verify_phi_placement,
    verify_ssa,
)
from .interp import run_tac, run_ssa

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
    "SSAProgram",
    "to_ssa",
    "to_ssa_cfg",
    "compute_defsites",
    "place_phis",
    "iterated_dominance_frontier",
    "verify_phi_placement",
    "verify_ssa",
    "run_tac",
    "run_ssa",
]
