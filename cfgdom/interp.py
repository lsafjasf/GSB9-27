"""解释器：分别执行原始 TAC 与 SSA 形式，用于优化前后对拍。

两个解释器共用 cfgdom.expr 的求值器，保证表达式语义完全一致。
变量在读而未写时视为程序输入：值取自 inputs（缺省为 0）。
"""

from __future__ import annotations

from typing import Dict, Optional

from .expr import eval_expr
from .ssa import SSAProgram, cjump_cond, ret_arg, split_assign
from .tac import parse_tac


class _Env(dict):
    """变量环境：未命中时回退到输入表，再缺省为 0。"""

    def __init__(self, inputs: Dict[str, int]):
        super().__init__()
        self._inputs = inputs

    def __missing__(self, key: str):
        return self._inputs.get(key, 0)


def run_tac(source: str, inputs: Optional[Dict[str, int]] = None,
            max_steps: int = 1_000_000):
    """解释执行原始 TAC，返回 ret 的值（自然结束返回 None）。"""
    inputs = inputs or {}
    instrs = parse_tac(source)
    labels: Dict[str, int] = {}
    for i, ins in enumerate(instrs):
        for lab in getattr(ins, "labels", []):
            labels[lab] = i

    env = _Env(inputs)
    pc = 0
    steps = 0
    while pc < len(instrs):
        steps += 1
        if steps > max_steps:
            raise RuntimeError("超过最大步数，疑似死循环")
        ins = instrs[pc]
        if ins.kind == "assign":
            pa = split_assign(ins.text)
            if pa is None:
                raise SyntaxError(f"无法解析的赋值语句: {ins.text!r}")
            dest, rhs = pa
            env[dest] = eval_expr(rhs, env)
            pc += 1
        elif ins.kind == "cjump":
            if eval_expr(cjump_cond(ins.text), env):
                pc = labels[ins.target]
            else:
                pc += 1
        elif ins.kind == "jump":
            pc = labels[ins.target]
        elif ins.kind == "ret":
            arg = ret_arg(ins.text)
            return eval_expr(arg, env) if arg else None
    return None


def run_ssa(prog: SSAProgram, inputs: Optional[Dict[str, int]] = None,
            max_steps: int = 1_000_000):
    """解释执行 SSA 程序。

    进入基本块时先按“来路前驱”并行解析全部 phi，再顺序执行块内指令。
    每个变量的 v_0 版本用 inputs[v]（缺省 0）初始化，对应程序输入。
    """
    inputs = inputs or {}
    env = _Env({})
    for v in prog.vars:
        env[f"{v}_0"] = inputs.get(v, 0)

    prev: Optional[int] = None
    cur = prog.entry
    steps = 0
    while True:
        steps += 1
        if steps > max_steps:
            raise RuntimeError("超过最大步数，疑似死循环")
        blk = prog.block(cur)
        if blk.phis:
            if prev is None:
                # 首入入口块（入口含回边时）：phi 取输入版本
                vals = [(phi.dest, env[f"{phi.var}_0"]) for phi in blk.phis]
            else:
                vals = [(phi.dest, env[phi.args[prev]]) for phi in blk.phis]
            for dest, val in vals:  # 并行语义：先全部求值再统一绑定
                env[dest] = val
        for text in blk.instrs:
            dest, rhs = split_assign(text)  # type: ignore[misc]
            env[dest] = eval_expr(rhs, env)
        if blk.term_kind == "jump":
            prev, cur = cur, blk.jump_target  # type: ignore[assignment]
        elif blk.term_kind == "cjump":
            nxt = blk.jump_target if eval_expr(blk.cond or "0", env) else blk.fall_target
            prev, cur = cur, nxt  # type: ignore[assignment]
        elif blk.term_kind == "ret":
            return eval_expr(blk.ret_expr, env) if blk.ret_expr else None
        else:  # fall
            if blk.fall_target is None:
                return None
            prev, cur = cur, blk.fall_target
