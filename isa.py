"""A small example target ISA used by the demo, tests and benchmarks."""

from iselect import Instr, Pat

A = Pat.any


def _is_power_of_two(node):
    v = node.val
    return isinstance(v, int) and v > 0 and (v & (v - 1)) == 0


def default_isa():
    """A toy ISA: immediates/registers, ALU ops, fused ops and divmod."""
    return [
        Instr("imm", 1, Pat("const")),                       # load constant
        Instr("mov", 0, Pat("reg")),                         # register input
        Instr("add", 1, Pat("add", (A(), A()))),
        Instr("addi", 1, Pat("add", (A(), Pat("const")))),   # const is consumed
        Instr("sub", 1, Pat("sub", (A(), A()))),
        Instr("neg", 1, Pat("neg", (A(),))),
        Instr("mul", 3, Pat("mul", (A(), A()))),
        Instr("shl", 1, Pat("mul", (A(), Pat("const", pred=_is_power_of_two)))),
        Instr("madd", 3, Pat("add", (Pat("mul", (A(), A())), A()))),
        Instr("div", 8, Pat("div", (A(), A()))),
        Instr("mod", 8, Pat("mod", (A(), A()))),
        Instr("divmod", 9, [Pat("div", (Pat.V(0), Pat.V(1))),
                            Pat("mod", (Pat.V(0), Pat.V(1)))]),
    ]
