"""控制流图构建：基本块划分、边连接、不可达块标记。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set

from .tac import Instr, parse_tac


@dataclass
class BasicBlock:
    bid: int
    instrs: List[Instr] = field(default_factory=list)
    succs: List[int] = field(default_factory=list)
    preds: List[int] = field(default_factory=list)
    reachable: bool = True

    def __repr__(self) -> str:  # pragma: no cover
        return f"B{self.bid}"


class CFG:
    def __init__(self, blocks: List[BasicBlock], entry: int):
        self.blocks = blocks
        self.entry = entry
        self.reachable_ids: List[int] = []
        self.unreachable: List[BasicBlock] = []

    def block(self, bid: int) -> BasicBlock:
        return self.blocks[bid]


def _split_blocks(instrs: List[Instr]) -> List[BasicBlock]:
    """经典 leader 算法：首指令、跳转目标、跳转的下一条。"""
    leaders: Set[int] = {0}
    label_pos: Dict[str, int] = {}
    for i, ins in enumerate(instrs):
        for lab in getattr(ins, "labels", []):
            label_pos[lab] = i
    for i, ins in enumerate(instrs):
        if ins.kind in ("jump", "cjump"):
            if ins.target not in label_pos:
                raise SyntaxError(f"未定义的标签: {ins.target!r}")
            leaders.add(label_pos[ins.target])
            if i + 1 < len(instrs):
                leaders.add(i + 1)
    blocks: List[BasicBlock] = []
    cur = BasicBlock(bid=0)
    for i, ins in enumerate(instrs):
        if i in leaders and cur.instrs:
            blocks.append(cur)
            cur = BasicBlock(bid=len(blocks))
        cur.instrs.append(ins)
    if cur.instrs:
        blocks.append(cur)
    for new_id, b in enumerate(blocks):
        b.bid = new_id
    return blocks, label_pos


def build_cfg(source: str) -> CFG:
    """从 TAC 源码构建 CFG，并标记不可达块。"""
    instrs = parse_tac(source)
    if not instrs:
        raise ValueError("空的指令序列")
    blocks, label_pos = _split_blocks(instrs)

    # 标签 -> 块（用指令身份定位块）
    label_block: Dict[str, int] = {}
    instr_block: Dict[int, int] = {}
    for b in blocks:
        for ins in b.instrs:
            instr_block[id(ins)] = b.bid
    for lab, pos in label_pos.items():
        label_block[lab] = instr_block[id(instrs[pos])]

    # 连边
    for b in blocks:
        last = b.instrs[-1]
        targets: List[int] = []
        if last.kind == "jump":
            targets = [label_block[last.target]]
        elif last.kind == "cjump":
            targets = [label_block[last.target]]
            if b.bid + 1 < len(blocks):
                targets.append(b.bid + 1)
        elif last.kind == "ret":
            targets = []
        else:  # 顺序下落
            if b.bid + 1 < len(blocks):
                targets = [b.bid + 1]
        for t in targets:
            if t not in b.succs:
                b.succs.append(t)
                blocks[t].preds.append(b.bid)

    cfg = CFG(blocks, entry=0)

    # 可达性（从入口 DFS）
    seen: Set[int] = set()
    stack = [cfg.entry]
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        stack.extend(blocks[u].succs)
    for b in blocks:
        if b.bid in seen:
            cfg.reachable_ids.append(b.bid)
        else:
            b.reachable = False
            cfg.unreachable.append(b)
    return cfg
