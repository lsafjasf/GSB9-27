"""活跃变量分析（liveness analysis）：基于控制流图的后向数据流分析。

仅使用 Python 标准库。

模型
----
- 每条指令 Instr 有 defs（定值变量集）、uses（使用变量集）、may_throw（是否可能抛异常）。
- 每个基本块 BasicBlock 有正常后继 succs 与异常后继 exc_succs（异常跳转边）。
- 块的 live_out = 所有后继（正常 + 异常）live_in 的并集（保守、声音的近似：
  异常边按块级处理，即认为块尾与异常出口处的活跃集合一致）。
- 块的 live_in = use[B] ∪ (live_out − def[B])，其中 use/def 为块内向上暴露使用/定值。

两个求解器：
- solve_liveness_worklist：按"逆 CFG 的逆后序"初始化优先级的 worklist 混沌迭代。
- solve_liveness_naive：固定块序的逐点（round-robin）迭代，整轮无变化才停止。

两者都收敛到数据流方程组的最小不动点，因此结果必须完全一致（对拍依据）。
"""

from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Instr:
    defs: frozenset = frozenset()
    uses: frozenset = frozenset()
    may_throw: bool = False


def _block_def_use(instrs):
    """前向扫描计算块级 def/use（向上暴露使用）。"""
    defs = set()
    uses = set()
    for ins in instrs:
        uses |= ins.uses - defs
        defs |= ins.defs
    return defs, uses


class BasicBlock:
    __slots__ = ("id", "instrs", "succs", "exc_succs", "defs", "uses")

    def __init__(self, bid, instrs=(), succs=(), exc_succs=()):
        self.id = bid
        self.instrs = list(instrs)
        self.succs = list(succs)          # 正常后继块 id
        self.exc_succs = list(exc_succs)  # 异常后继块 id（异常跳转边）
        self.defs, self.uses = _block_def_use(self.instrs)

    def all_succs(self):
        return list(self.succs) + list(self.exc_succs)


@dataclass
class LivenessResult:
    live_in: dict   # block id -> set[str]
    live_out: dict  # block id -> set[str]
    rounds: int     # naive: 整轮扫描次数；worklist: 无意义，置 0
    updates: int    # 块传递函数求值次数（工作量指标）

    def same_sets(self, other):
        return self.live_in == other.live_in and self.live_out == other.live_out


def _backward_priority_order(blocks, succs, preds):
    """后向分析的优先顺序：逆 CFG 上的逆后序（RPO of the transpose graph）。

    对后向分析，信息沿边反向流动，因此让"后继先于前驱"被处理能最快传播。
    等价于在逆图上做 RPO。从所有出口块（无后继）开始做逆图 DFS 后序，
    再反转；不可达块（从入口不可达、或不到达出口）也会被覆盖，
    保证所有块都进入初始 worklist。
    """
    visited = set()
    post = []
    roots = [b for b in blocks if not succs[b]] + list(blocks)
    for root in roots:
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(sorted(preds[root])))]
        while stack:
            node, it = stack[-1]
            nxt = None
            for nb in it:
                if nb not in visited:
                    nxt = nb
                    break
            if nxt is None:
                post.append(node)
                stack.pop()
            else:
                visited.add(nxt)
                stack.append((nxt, iter(sorted(preds[nxt]))))
    post.reverse()
    return post


def solve_liveness_worklist(blocks):
    """Worklist 混沌迭代求解活跃变量方程。

    收敛依据：格 (2^Vars, ⊆) 有限高度（≤ |Vars|），传递函数单调，
    且每次更新只增不减（对旧值取并的归纳可证），构成有限上升链，
    故 worklist 必在有限步内清空；混沌迭代定理保证结果为最小不动点。
    """
    live_in = {b: set() for b in blocks}
    live_out = {b: set() for b in blocks}
    if not blocks:
        return LivenessResult(live_in, live_out, rounds=0, updates=0)

    succs = {b: blk.all_succs() for b, blk in blocks.items()}
    preds = {b: [] for b in blocks}
    for b, ss in succs.items():
        for s in ss:
            preds[s].append(b)

    order = _backward_priority_order(blocks, succs, preds)
    work = deque(order)
    in_work = set(order)
    updates = 0
    while work:
        b = work.popleft()
        in_work.discard(b)
        blk = blocks[b]
        out = set()
        for s in succs[b]:
            out |= live_in[s]
        new_in = blk.uses | (out - blk.defs)
        updates += 1
        if new_in != live_in[b] or out != live_out[b]:
            live_in[b] = new_in
            live_out[b] = out
            for p in preds[b]:
                if p not in in_work:
                    in_work.add(p)
                    work.append(p)
    return LivenessResult(live_in, live_out, rounds=0, updates=updates)


def solve_liveness_naive(blocks):
    """朴素逐点迭代：固定块序反复整轮扫描，直到整轮无任何变化。

    与 worklist 版本解同一组方程，单调上升、格有限，故同样终止于
    最小不动点；二者结果必然相同（这是对拍测试的理论依据）。
    """
    live_in = {b: set() for b in blocks}
    live_out = {b: set() for b in blocks}
    ids = list(blocks)
    rounds = 0
    updates = 0
    changed = True
    while changed:
        changed = False
        rounds += 1
        for b in ids:
            blk = blocks[b]
            out = set()
            for s in blk.succs:
                out |= live_in[s]
            for s in blk.exc_succs:
                out |= live_in[s]
            new_in = blk.uses | (out - blk.defs)
            updates += 1
            if new_in != live_in[b] or out != live_out[b]:
                live_in[b] = new_in
                live_out[b] = out
                changed = True
    return LivenessResult(live_in, live_out, rounds=rounds, updates=updates)
