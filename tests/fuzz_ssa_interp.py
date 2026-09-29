"""SSA 随机对拍：原始 TAC 解释器 vs SSA 解释器，同一输入比结果。

每轮随机生成一个保证终止的结构化程序（赋值 / if-else / 计数循环），
随机一组输入，然后：
  1. 构造 SSA，并用暴力支配边界 df_brute 逐变量核对 phi 位置；
  2. verify_ssa 校验重命名后的定义-使用对应关系；
  3. run_tac 与 run_ssa 在同一输入下运行，返回值必须一致。

运行：python3 tests/fuzz_ssa_interp.py [轮数] [种子]
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cfgdom import (
    build_cfg,
    df_brute,
    dom_brute,
    run_ssa,
    run_tac,
    to_ssa_cfg,
    verify_phi_placement,
    verify_ssa,
)

VARS = ["a", "b", "c", "d", "s"]


class ProgramGen:
    """生成保证终止的随机结构化 TAC 程序。"""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.lines = ["entry:"]
        self.label_n = 0
        self.loop_n = 0

    def label(self) -> str:
        self.label_n += 1
        return f"L{self.label_n}"

    def emit(self, text: str) -> None:
        self.lines.append("  " + text)

    def expr(self, depth: int = 0) -> str:
        r = self.rng.random()
        if depth >= 2 or r < 0.45:
            if self.rng.random() < 0.7:
                return self.rng.choice(VARS)
            return str(self.rng.randint(-5, 9))
        op = self.rng.choice(["+", "-", "*"])
        return f"{self.expr(depth + 1)} {op} {self.expr(depth + 1)}"

    def cond(self) -> str:
        op = self.rng.choice(["<", "<=", ">", ">=", "==", "!="])
        return f"{self.expr(1)} {op} {self.expr(1)}"

    def stmts(self, n: int, depth: int) -> None:
        for _ in range(n):
            r = self.rng.random()
            if r < 0.45 or depth >= 2:
                self.emit(f"{self.rng.choice(VARS)} = {self.expr()}")
            elif r < 0.72:  # if-else 菱形
                l_else, l_end = self.label(), self.label()
                self.emit(f"if {self.cond()} goto {l_else}")
                self.stmts(self.rng.randint(0, 2), depth + 1)
                self.emit(f"goto {l_end}")
                self.lines.append(f"{l_else}:")
                self.stmts(self.rng.randint(0, 2), depth + 1)
                self.lines.append(f"{l_end}:")
            else:  # 计数循环（计数器专属变量，保证终止）
                lc = f"lc{self.loop_n}"
                self.loop_n += 1
                head = self.label()
                self.emit(f"{lc} = 0")
                self.lines.append(f"{head}:")
                self.stmts(self.rng.randint(1, 3), depth + 1)
                self.emit(f"{lc} = {lc} + 1")
                self.emit(f"if {lc} < {self.rng.randint(1, 5)} goto {head}")

    def gen(self) -> str:
        self.stmts(self.rng.randint(2, 6), 0)
        self.emit(f"ret {self.rng.choice(VARS)}")
        return "\n".join(self.lines)


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260929
    rng = random.Random(seed)
    n_phi_total = 0
    n_with_phi = 0
    for it in range(rounds):
        src = ProgramGen(rng).gen()
        inputs = {v: rng.randint(-10, 10) for v in VARS}
        cfg = build_cfg(src)
        prog = to_ssa_cfg(cfg)

        tag = f"iter={it} seed={seed}"
        # phi 位置：与暴力支配边界独立复算核对
        verify_phi_placement(prog, cfg, df=df_brute(cfg, dom_brute(cfg)))
        # 重命名：定义唯一 / 使用有定义 / 定义支配使用
        verify_ssa(prog, cfg)
        # 优化前后对拍
        r1 = run_tac(src, inputs)
        r2 = run_ssa(prog, inputs)
        assert r1 == r2, f"{tag}: 输入 {inputs}: TAC={r1} SSA={r2}\n{src}"

        n_phi = sum(len(b.phis) for b in prog.blocks.values())
        n_phi_total += n_phi
        n_with_phi += n_phi > 0
    print(f"对拍通过: {rounds} 轮 (seed={seed})")
    print(f"  含 phi 的程序: {n_with_phi}/{rounds}")
    print(f"  phi 总数:      {n_phi_total}")
    print(f"  每轮校验: phi位置(vs暴力DF) + SSA不变式 + 双解释器同输入同结果")


if __name__ == "__main__":
    main()
