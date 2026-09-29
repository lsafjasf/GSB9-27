from .cfg import CFG, Block, Instr, parse_tac
from .dom import (analyze, compute_idom, dominator_sets, dominator_tree,
                  dominance_frontiers, reverse_postorder)
from .brute import (brute_dominator_sets, brute_idom,
                    brute_dominance_frontiers)
