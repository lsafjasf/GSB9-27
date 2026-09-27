"""三地址码 (TAC) 解析。

行式文法（大小写敏感，# 后为注释）：
    L1:                 标签定义
    x = a + b           普通赋值（任意非控制语句原样保留）
    if a < b goto L2    条件跳转（条件部分不求值），后继为 L2 与顺序下一条
    goto L3             无条件跳转
    ret x               返回（出口）
函数末尾未以控制指令结束时，隐式视为出口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Instr:
    kind: str               # 'assign' | 'cjump' | 'jump' | 'ret'
    text: str               # 原始文本
    target: Optional[str] = None   # jump/cjump 的目标标签


def parse_tac(source: str) -> List[Instr]:
    """把 TAC 源码解析成指令序列，标签行展开为 (label, instr) 位置信息。

    返回指令列表；标签到指令下标的映射由 build_cfg 阶段通过
    指令上的 label 属性获得，这里把标签挂到下一条指令上。
    """
    instrs: List[Instr] = []
    pending_labels: List[str] = []
    for lineno, raw in enumerate(source.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.endswith(":") and " " not in line[:-1]:
            pending_labels.append(line[:-1])
            continue
        head = line.split(None, 1)[0]
        if head == "goto":
            target = line.split(None, 1)[1].strip()
            instr = Instr("jump", line, target=target)
        elif head == "if":
            if " goto " not in (" " + line + " "):
                raise SyntaxError(f"line {lineno}: if 语句缺少 goto: {line!r}")
            cond_part, target = line.rsplit("goto", 1)
            instr = Instr("cjump", line, target=target.strip())
        elif head == "ret" or head == "return":
            instr = Instr("ret", line)
        else:
            instr = Instr("assign", line)
        instr.labels = pending_labels  # type: ignore[attr-defined]
        pending_labels = []
        instrs.append(instr)
    if pending_labels:
        # 悬空的末尾标签：挂到一条隐式 ret 上，保证标签可寻址
        instr = Instr("ret", "ret")
        instr.labels = pending_labels  # type: ignore[attr-defined]
        instrs.append(instr)
    return instrs
