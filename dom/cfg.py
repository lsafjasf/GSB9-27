"""Control-flow graph construction from three-address code (TAC).

TAC text format (one instruction per line, '#' starts a comment):

    <label>:                       # block label
    <dst> = <a> <op> <b>           # assignment (any rhs shape is accepted)
    if <cond> goto <L1> else goto <L2>
    goto <L>
    return

A new basic block starts at every label and right after every control
transfer.  Blocks fall through to the next block unless they end with an
explicit control transfer.
"""

from dataclasses import dataclass, field


@dataclass
class Instr:
    kind: str                      # 'assign' | 'cbranch' | 'jump' | 'return'
    text: str
    target: str = None             # jump target
    true_target: str = None        # cbranch taken target
    false_target: str = None       # cbranch not-taken target


@dataclass
class Block:
    name: str
    instrs: list = field(default_factory=list)
    succs: list = field(default_factory=list)   # list[str], program order
    preds: list = field(default_factory=list)   # list[str]


class CFG:
    def __init__(self):
        self.blocks = {}           # name -> Block
        self.order = []            # block names in program order
        self.entry = None          # name of entry block

    def add_block(self, block):
        self.blocks[block.name] = block
        self.order.append(block.name)

    def reachable_names(self):
        """Names of blocks reachable from the entry block."""
        seen = set()
        stack = [self.entry]
        while stack:
            name = stack.pop()
            if name in seen or name not in self.blocks:
                continue
            seen.add(name)
            stack.extend(self.blocks[name].succs)
        return seen

    def unreachable_names(self):
        reachable = self.reachable_names()
        return [n for n in self.order if n not in reachable]


def _parse_instr(line):
    if line.startswith("if ") and " goto " in line:
        # if <cond> goto L1 else goto L2
        head, _, rest = line.partition(" goto ")
        true_t, _, rest2 = rest.partition(" else goto ")
        return Instr("cbranch", line, true_target=true_t.strip(),
                     false_target=rest2.strip())
    if line.startswith("goto "):
        return Instr("jump", line, target=line[len("goto "):].strip())
    if line == "return" or line.startswith("return "):
        return Instr("return", line)
    return Instr("assign", line)


def parse_tac(text):
    """Parse TAC source text into a CFG with basic blocks and edges."""
    # ---- first pass: split source lines into (label, instr) items ----
    items = []                     # (label_or_None, Instr_or_None)
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        while True:                # allow several labels on one line
            head, sep, rest = line.partition(":")
            if sep and head.strip() and " " not in head.strip() \
               and "=" not in head:
                items.append((head.strip(), None))
                line = rest.strip()
                if not line:
                    break
            else:
                break
        if line:
            items.append((None, _parse_instr(line)))

    if not items:
        raise ValueError("empty program")

    # ---- second pass: form basic blocks ----
    cfg = CFG()
    cur = None
    anon = 0

    def new_block(name=None):
        nonlocal cur, anon
        if name is None:
            anon += 1
            name = "B%d" % anon
            while name in cfg.blocks:
                anon += 1
                name = "B%d" % anon
        cur = Block(name)
        cfg.add_block(cur)

    pending_label = None
    for label, instr in items:
        if label is not None:
            if pending_label is not None:
                raise ValueError("consecutive labels: %s, %s"
                                 % (pending_label, label))
            pending_label = label
            continue
        starts_new = (cur is None) or (
            cur.instrs and cur.instrs[-1].kind in ("cbranch", "jump", "return"))
        if starts_new:
            new_block(pending_label)
        elif pending_label is not None:
            # label in the middle of a fall-through block: split here
            new_block(pending_label)
        pending_label = None
        cur.instrs.append(instr)
    if pending_label is not None:
        new_block(pending_label)   # trailing empty labelled block

    cfg.entry = cfg.order[0]

    # ---- third pass: wire edges ----
    for i, name in enumerate(cfg.order):
        block = cfg.blocks[name]
        fall = cfg.order[i + 1] if i + 1 < len(cfg.order) else None
        if not block.instrs:
            if fall is not None:   # empty block falls through
                block.succs.append(fall)
            continue
        last = block.instrs[-1]
        if last.kind == "cbranch":
            block.succs.append(last.true_target)
            block.succs.append(last.false_target)
        elif last.kind == "jump":
            block.succs.append(last.target)
        elif last.kind == "return":
            pass
        elif fall is not None:
            block.succs.append(fall)

    for name in cfg.order:
        for succ in cfg.blocks[name].succs:
            if succ not in cfg.blocks:
                raise ValueError("jump to undefined label %r (from %r)"
                                 % (succ, name))
            cfg.blocks[succ].preds.append(name)
    return cfg
