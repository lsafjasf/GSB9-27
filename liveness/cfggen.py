"""CFG 生成器：随机图（含循环/异常边/不可达块）与结构化基准图。"""

import random

from liveness import BasicBlock, Instr


def random_cfg(rng, n_blocks, n_vars=12, fwd_prob=0.06, back_prob=0.04,
               exc_prob=0.05, unreachable_frac=0.15):
    """随机 CFG。

    - 块 id 为 0..n-1；以 0.7 概率加 i -> i+1 链边保持主干连通。
    - 前向边 / 回边（构成循环）/ 异常边均按概率随机添加。
    - 末尾 unreachable_frac 比例的块划入"不可达岛"：岛内可有边（含自环），
      但可达区不会指向岛内，保证其从入口不可达。
    """
    var_pool = ["v%d" % i for i in range(n_vars)]
    blocks = {}
    for i in range(n_blocks):
        instrs = []
        for _ in range(rng.randint(0, 4)):
            defs = frozenset(v for v in var_pool if rng.random() < 0.25)
            uses = frozenset(v for v in var_pool if rng.random() < 0.30)
            instrs.append(Instr(defs=defs, uses=uses,
                                may_throw=rng.random() < 0.2))
        blocks[i] = BasicBlock(i, instrs)

    n_unreach = int(n_blocks * unreachable_frac)
    boundary = n_blocks - n_unreach  # [0, boundary) 可达区，[boundary, n) 不可达岛

    for i in range(n_blocks):
        hi = boundary if i < boundary else n_blocks  # 本块允许指向的最大 id 区间
        lo = 0 if i < boundary else boundary
        if i + 1 < hi and rng.random() < 0.7:
            blocks[i].succs.append(i + 1)
        for j in range(lo, hi):
            if j == i:
                if rng.random() < back_prob * 0.3:  # 偶尔自环
                    blocks[i].succs.append(j)
            elif j > i and rng.random() < fwd_prob:
                blocks[i].succs.append(j)
            elif j < i and rng.random() < back_prob:
                blocks[i].succs.append(j)
        if any(ins.may_throw for ins in blocks[i].instrs) and rng.random() < exc_prob:
            # 异常边：指向本区内随机块（模拟异常处理器入口）
            tgt = rng.randrange(lo, hi) if hi > lo else i
            blocks[i].exc_succs.append(tgt)
    return blocks


def gen_chain(n, n_vars=8):
    """只有前向边的长链：0 -> 1 -> ... -> n-1。"""
    blocks = {}
    for i in range(n):
        d = frozenset({"v%d" % (i % n_vars)})
        u = frozenset({"v%d" % ((i + 1) % n_vars)})
        succ = [i + 1] if i + 1 < n else []
        blocks[i] = BasicBlock(i, [Instr(defs=d, uses=u)], succs=succ)
    return blocks


def gen_nested_loops(depth, body, n_vars=16, with_exceptions=True):
    """深层嵌套循环。

    第 k 层循环占连续 body 个块：header -> body... -> latch，
    latch 回边到本层 header，并出边到下一层 header；
    最内层 latch 出边到唯一出口块。可选异常边：每个 latch 可能抛到
    统一异常处理器，处理器再跳回最外层 header（构成异常回路）。
    """
    blocks = {}
    bid = 0
    headers = []
    latches = []
    for level in range(depth):
        header = bid
        headers.append(header)
        for j in range(body):
            i = bid
            d = frozenset({"v%d" % ((i + level) % n_vars)})
            u = frozenset({"v%d" % ((i + 1) % n_vars),
                           "v%d" % ((i + 3) % n_vars)})
            succ = [i + 1] if j + 1 < body else []
            blocks[i] = BasicBlock(i, [Instr(defs=d, uses=u)], succs=succ)
            bid += 1
        latches.append(bid - 1)
    exit_id = bid
    blocks[exit_id] = BasicBlock(exit_id, [Instr(uses=frozenset({"v0"}))])
    handler_id = bid + 1
    if with_exceptions:
        blocks[handler_id] = BasicBlock(
            handler_id, [Instr(defs=frozenset({"v1"}), uses=frozenset({"v2"}))],
            succs=[headers[0]])  # 处理器跳回最外层循环头，构成异常回路
    for level in range(depth):
        latch = latches[level]
        blocks[latch].succs.append(headers[level])          # 回边
        nxt = headers[level + 1] if level + 1 < depth else exit_id
        blocks[latch].succs.append(nxt)                     # 退出本层
        if with_exceptions:
            blocks[latch].instrs.append(Instr(may_throw=True))
            blocks[latch].exc_succs.append(handler_id)
    return blocks


def count_edges(blocks):
    return sum(len(b.succs) + len(b.exc_succs) for b in blocks.values())
