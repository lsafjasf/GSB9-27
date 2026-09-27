"""Worked example: fused multiply-add, shifts, sharing and divmod."""

from iselect import Node, select
from isa import default_isa


def main():
    isa = default_isa()
    x, y = Node("reg", val="x"), Node("reg", val="y")
    a = Node("reg", val="a")

    # q and r are both shared across roots -> they are materialized once,
    # and the multi-output divmod instruction can cover both.
    q = Node("div", (x, y))
    r = Node("mod", (x, y))
    roots = [
        Node("add", (q, r)),                    # add(q, r)
        Node("sub", (q, r)),                    # sub(q, r)
        Node("mul", (q, Node("const", val=8))), # q * 8 -> shift
        Node("add", (Node("mul", (a, Node("const", val=4))), x)),  # madd
    ]

    sel = select(roots, isa)
    print(sel)
    print()
    print("emitted %d instructions, total cost %d"
          % (len(sel.steps), sel.total_cost))


if __name__ == "__main__":
    main()
