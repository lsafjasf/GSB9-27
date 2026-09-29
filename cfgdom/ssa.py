"""SSA（静态单赋值）构造。

管线：CFG → 支配树/支配边界 → phi 插入（迭代支配边界，Cytron 工作表算法）
→ 变量重命名（支配树 DFS + 每变量版本栈）→ 可读的 SSA 文本。

约定：
- 原始变量 v 的每个定义得到新版本 v_1, v_2, ...；
- v_0 是隐式的"入口版本"，表示程序输入（未定义即引用的变量）；
- phi 只插在进入迭代支配边界的汇合点（可达前驱 >= 2 的块）；
- 不可达块不参与 SSA。

校验器：
- verify_phi_placement：独立复算每个变量的迭代支配边界，逐变量核对 phi 位置；
- verify_ssa：每个版本恰好定义一次；每次使用的版本都有定义；
  定义支配使用（phi 参数按边判定）；phi 参数与可达前驱一一对应。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .cfg import CFG, build_cfg
from .dom import dominators, dominance_frontier, dominator_tree
from .expr import expr_vars, rename_expr
from .tac import Instr

ASSIGN_RE = re.compile(r"^([A-Za-z_]\w*)\s*=(?!=)\s*(.*)$")


def split_assign(text: str) -> Optional[Tuple[str, str]]:
    """把 'x = expr' 拆成 (dest, rhs)；不是赋值语句则返回 None。"""
    m = ASSIGN_RE.match(text.strip())
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def cjump_cond(text: str) -> str:
    """从 'if <cond> goto L' 中取出条件表达式文本。"""
    body = text.strip()[2:].strip()
    cond, _ = body.rsplit("goto", 1)
    return cond.strip()


def ret_arg(text: str) -> Optional[str]:
    parts = text.strip().split(None, 1)
    return parts[1].strip() if len(parts) > 1 else None


def instr_def(instr: Instr) -> Optional[str]:
    if instr.kind == "assign":
        pa = split_assign(instr.text)
        if pa is None:
            raise SyntaxError(f"无法解析的赋值语句: {instr.text!r}")
        return pa[0]
    return None


def instr_uses(instr: Instr) -> List[str]:
    if instr.kind == "assign":
        pa = split_assign(instr.text)
        if pa is None:
            raise SyntaxError(f"无法解析的赋值语句: {instr.text!r}")
        return expr_vars(pa[1])
    if instr.kind == "cjump":
        return expr_vars(cjump_cond(instr.text))
    if instr.kind == "ret":
        arg = ret_arg(instr.text)
        return expr_vars(arg) if arg else []
    return []


# ---------------------------------------------------------------- phi 插入

def compute_defsites(cfg: CFG) -> Dict[str, Set[int]]:
    """每个变量在哪些（可达）块中被定义。"""
    defsites: Dict[str, Set[int]] = {}
    for bid in cfg.reachable_ids:
        for ins in cfg.blocks[bid].instrs:
            d = instr_def(ins)
            if d is not None:
                defsites.setdefault(d, set()).add(bid)
    return defsites


def iterated_dominance_frontier(df: Dict[int, Set[int]], nodes: Set[int]) -> Set[int]:
    """独立复算用：节点集合的迭代支配边界 DF+（不动点定义）。"""
    result: Set[int] = set()
    work = list(nodes)
    seen = set(nodes)
    while work:
        x = work.pop()
        for y in df.get(x, ()):
            if y not in result:
                result.add(y)
                if y not in seen:
                    seen.add(y)
                    work.append(y)
    return result


def place_phis(cfg: CFG, df: Dict[int, Set[int]],
               defsites: Dict[str, Set[int]]) -> Dict[str, Set[int]]:
    """Cytron 工作表算法：变量 v 的 phi 位置 = defsites(v) 的迭代支配边界。"""
    placement: Dict[str, Set[int]] = {}
    for v, sites in defsites.items():
        placed: Set[int] = set()
        work = list(sites)
        queued = set(sites)
        while work:
            x = work.pop()
            for y in df.get(x, ()):
                if y not in placed:
                    placed.add(y)
                    if y not in queued:
                        queued.add(y)
                        work.append(y)
        if placed:
            placement[v] = placed
    return placement


# ---------------------------------------------------------------- 数据结构

@dataclass
class Phi:
    var: str                      # 原变量名
    block: int                    # 所在块
    dest: str = ""                # 重命名后的定义名（如 s_3）
    args: Dict[int, str] = field(default_factory=dict)  # 前驱块号 -> 版本名


@dataclass
class SSABlock:
    bid: int
    preds: List[int]
    succs: List[int]
    phis: List[Phi] = field(default_factory=list)
    instrs: List[str] = field(default_factory=list)   # 重命名后的赋值语句
    term_kind: str = "fall"                            # jump|cjump|ret|fall
    cond: Optional[str] = None                         # cjump 的重命名条件
    jump_target: Optional[int] = None
    fall_target: Optional[int] = None
    ret_expr: Optional[str] = None                     # ret 的重命名表达式


@dataclass
class SSAProgram:
    blocks: Dict[int, SSABlock]
    entry: int
    vars: List[str]                 # 程序中出现的全部原始变量
    input_vars: List[str]          # 无定义的变量（程序输入）
    defsites: Dict[str, Set[int]]
    placement: Dict[str, Set[int]]  # var -> phi 插入块集合

    def block(self, bid: int) -> SSABlock:
        return self.blocks[bid]

    def to_text(self) -> str:
        lines: List[str] = []
        for bid in sorted(self.blocks):
            b = self.blocks[bid]
            preds = ", ".join(f"B{p}" for p in b.preds) or "-"
            lines.append(f"B{bid}:  # preds: {preds}")
            for phi in b.phis:
                args = ", ".join(f"{phi.args[p]}@B{p}" for p in b.preds)
                lines.append(f"  {phi.dest} = phi({args})")
            for t in b.instrs:
                lines.append(f"  {t}")
            if b.term_kind == "jump":
                lines.append(f"  goto B{b.jump_target}")
            elif b.term_kind == "cjump":
                lines.append(f"  if {b.cond} goto B{b.jump_target}")
            elif b.term_kind == "ret":
                lines.append(f"  ret {b.ret_expr}" if b.ret_expr else "  ret")
            elif b.fall_target is not None:
                lines.append(f"  # -> B{b.fall_target}")
            else:
                lines.append("  # -> 出口")
        return "\n".join(lines)


# ---------------------------------------------------------------- 构造

def to_ssa(source: str) -> SSAProgram:
    return to_ssa_cfg(build_cfg(source))


def to_ssa_cfg(cfg: CFG) -> SSAProgram:
    idom, children = dominator_tree(cfg)
    df = dominance_frontier(cfg)
    defsites = compute_defsites(cfg)
    placement = place_phis(cfg, df, defsites)

    reach = set(cfg.reachable_ids)

    # 标签 -> 块号（重命名跳转目标用）
    label_block: Dict[str, int] = {}
    for bid in cfg.reachable_ids:
        for ins in cfg.blocks[bid].instrs:
            for lab in getattr(ins, "labels", []):
                label_block[lab] = bid

    # 全部变量（定义 ∪ 使用）
    all_vars: Set[str] = set(defsites)
    for bid in cfg.reachable_ids:
        for ins in cfg.blocks[bid].instrs:
            all_vars.update(instr_uses(ins))
    input_vars = sorted(v for v in all_vars if v not in defsites)

    # phi 节点骨架
    phis: Dict[int, List[Phi]] = {bid: [] for bid in reach}
    for v, blocks in placement.items():
        for bid in blocks:
            phis[bid].append(Phi(var=v, block=bid))
    for bid in phis:
        phis[bid].sort(key=lambda p: p.var)

    # ---- 重命名：支配树 DFS + 每变量版本栈，栈底 v_0 表示输入版本 ----
    counters = {v: 1 for v in all_vars}
    stacks: Dict[str, List[str]] = {v: [f"{v}_0"] for v in all_vars}
    out: Dict[int, SSABlock] = {}

    def new_name(v: str) -> str:
        name = f"{v}_{counters[v]}"
        counters[v] += 1
        stacks[v].append(name)
        return name

    def top(v: str) -> str:
        return stacks[v][-1]

    def visit(bid: int) -> None:
        pushed: List[str] = []
        blk = cfg.blocks[bid]
        sb = SSABlock(
            bid=bid,
            preds=sorted(p for p in blk.preds if p in reach),
            succs=[s for s in blk.succs if s in reach],
        )
        out[bid] = sb
        for phi in phis[bid]:
            phi.dest = new_name(phi.var)
            pushed.append(phi.var)
        sb.phis = phis[bid]

        for ins in blk.instrs:
            if ins.kind == "assign":
                dest, rhs = split_assign(ins.text)  # type: ignore[misc]
                new_rhs = rename_expr(rhs, top)
                new_dest = new_name(dest)
                pushed.append(dest)
                sb.instrs.append(f"{new_dest} = {new_rhs}")
            elif ins.kind == "cjump":
                sb.term_kind = "cjump"
                sb.cond = rename_expr(cjump_cond(ins.text), top)
                sb.jump_target = label_block[ins.target]
            elif ins.kind == "jump":
                sb.term_kind = "jump"
                sb.jump_target = label_block[ins.target]
            elif ins.kind == "ret":
                sb.term_kind = "ret"
                arg = ret_arg(ins.text)
                sb.ret_expr = rename_expr(arg, top) if arg else None

        if sb.term_kind in ("fall", "cjump"):
            sb.fall_target = bid + 1 if (bid + 1) in blk.succs else None

        # 为后继块的 phi 填写来自本块的参数
        for s in blk.succs:
            if s not in reach:
                continue
            for phi in phis[s]:
                phi.args[bid] = top(phi.var)

        for c in children[bid]:
            visit(c)
        for v in pushed:
            stacks[v].pop()

    visit(cfg.entry)

    return SSAProgram(
        blocks=out,
        entry=cfg.entry,
        vars=sorted(all_vars),
        input_vars=input_vars,
        defsites=defsites,
        placement=placement,
    )


# ---------------------------------------------------------------- 校验

def verify_phi_placement(prog: SSAProgram, cfg: CFG,
                         df: Optional[Dict[int, Set[int]]] = None) -> None:
    """逐变量核对：phi 位置 == 定义点集合的迭代支配边界，且都在汇合点。"""
    if df is None:
        df = dominance_frontier(cfg)
    defsites = compute_defsites(cfg)
    assert defsites == prog.defsites, "defsites 与 SSA 程序记录不一致"

    reach = set(cfg.reachable_ids)
    for v in sorted(defsites):
        expect = iterated_dominance_frontier(df, defsites[v])
        got = prog.placement.get(v, set())
        assert got == expect, (
            f"变量 {v}: phi 位置 {sorted(got)} != 迭代支配边界 {sorted(expect)}")
        for bid in got:
            n_preds = sum(1 for p in cfg.blocks[bid].preds if p in reach)
            assert n_preds >= 2, f"变量 {v}: phi 落在非汇合点 B{bid}"
    extra = set(prog.placement) - set(defsites)
    assert not extra, f"无定义变量却插了 phi: {extra}"


def verify_ssa(prog: SSAProgram, cfg: CFG) -> None:
    """核对 SSA 不变式：定义唯一、使用有定义、定义支配使用。"""
    dom = dominators(cfg)

    # 收集定义：name -> (块号, 位置)，phi 位置为 -1（块首）
    defs: Dict[str, Tuple[int, int]] = {}
    for bid, sb in prog.blocks.items():
        for phi in sb.phis:
            assert phi.dest not in defs, f"{phi.dest} 被重复定义"
            defs[phi.dest] = (bid, -1)
        for i, text in enumerate(sb.instrs):
            pa = split_assign(text)
            assert pa is not None, f"SSA 块内出现非赋值语句: {text!r}"
            assert pa[0] not in defs, f"{pa[0]} 被重复定义"
            defs[pa[0]] = (bid, i)

    def is_input(name: str) -> bool:
        base, _, suf = name.rpartition("_")
        return suf == "0" and base in prog.vars

    def check_use(name: str, use_bid: int, use_idx: int, edge_pred: Optional[int]) -> None:
        if is_input(name):
            return  # v_0 为隐式入口定义
        assert name in defs, f"使用了未定义的版本 {name}"
        def_bid, def_idx = defs[name]
        if edge_pred is not None:
            # phi 参数：使用发生在 edge_pred -> 本块 的边上，定义支配前驱块即可
            assert def_bid in dom[edge_pred], (
                f"{name} 的定义(B{def_bid})不支配 phi 边 B{edge_pred}->B{use_bid}")
        elif def_bid == use_bid:
            assert def_idx < use_idx, f"{name} 在 B{use_bid} 内先使用后定义"
        else:
            assert def_bid in dom[use_bid], (
                f"{name} 的定义(B{def_bid})不支配使用点 B{use_bid}")

    for bid, sb in prog.blocks.items():
        for i, text in enumerate(sb.instrs):
            _, rhs = split_assign(text)  # type: ignore[misc]
            for v in expr_vars(rhs):
                check_use(v, bid, i, None)
        if sb.term_kind == "cjump":
            for v in expr_vars(sb.cond or ""):
                check_use(v, bid, len(sb.instrs), None)
        if sb.term_kind == "ret" and sb.ret_expr:
            for v in expr_vars(sb.ret_expr):
                check_use(v, bid, len(sb.instrs), None)
        # phi 参数：与可达前驱一一对应
        for phi in sb.phis:
            assert set(phi.args) == set(sb.preds), (
                f"B{bid} 的 phi({phi.var}) 参数 {sorted(phi.args)} "
                f"与前驱 {sb.preds} 不一一对应")
            for p, name in phi.args.items():
                check_use(name, bid, -1, p)
