"""调用栈采样与聚合库（仅标准库）。

原理：
- 用 signal.setitimer 以固定频率向进程投递定时信号；
- 信号处理器里通过 sys._current_frames() 抓取目标线程当前调用栈；
- 每条栈自底（最外层）向顶（最内层）插入一棵聚合树，路径上每个节点
  total_count + 1，栈顶（叶子）节点 self_count + 1；
- 递归折叠：同一函数在栈中连续出现多次（直接递归）时折叠为一个节点，
  自身耗时记到该折叠节点上，不会产生 A->A->A->... 的无限链。

两种时钟（clock 参数）：
- "real"（默认）：ITIMER_REAL + SIGALRM，基于 hrtimer，kHz 级频率可全速率
  投递；按墙钟时间采样（含阻塞/睡眠，与 py-spy 默认口径一致）。
- "prof"：ITIMER_PROF + SIGPROF，按进程 CPU 时间采样，但 Linux 上 CPU
  定时器按内核 tick（常见 250Hz，即 4ms）结算，间隔小于 1 个 tick 时
  信号会被合并——loss_report() 会把这部分量化出来。
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from dataclasses import dataclass

_THIS_FILE = __file__


@dataclass(frozen=True)
class FrameKey:
    """一个函数的稳定标识：函数名 + 文件 + 函数定义行号。

    不用动态行号（f_lineno），否则同一函数内执行到不同行会被当成不同节点。
    """

    func: str
    filename: str
    firstlineno: int

    def label(self) -> str:
        return f"{self.func} ({self.filename}:{self.firstlineno})"


ROOT_KEY = FrameKey("<root>", "", 0)


class Node:
    __slots__ = ("key", "self_count", "total_count", "children")

    def __init__(self, key: FrameKey):
        self.key = key
        self.self_count = 0   # 采样命中时该节点是栈顶（自身耗时）的次数
        self.total_count = 0  # 采样命中时该节点出现在路径上（累计耗时）的次数
        self.children: dict[FrameKey, "Node"] = {}


def fold_frames(keys: list[FrameKey]) -> list[FrameKey]:
    """折叠连续重复的帧（直接递归）。

    [main, f, f, f, g] -> [main, f, g]
    f 的自身耗时（若 f 是栈顶）记到折叠后的唯一 f 节点上；
    f 的累计耗时只计一次，不会因递归深度被重复放大。
    """
    folded: list[FrameKey] = []
    for key in keys:
        if folded and folded[-1] == key:
            continue
        folded.append(key)
    return folded


class StackTree:
    """聚合树：根是虚拟节点，每条采样栈对应根到某节点的一条路径。"""

    def __init__(self):
        self.root = Node(ROOT_KEY)
        self.samples = 0

    def add(self, keys: list[FrameKey]) -> None:
        node = self.root
        node.total_count += 1
        for key in keys:
            child = node.children.get(key)
            if child is None:
                child = Node(key)
                node.children[key] = child
            child.total_count += 1
            node = child
        node.self_count += 1
        self.samples += 1

    def iter_nodes(self):
        """迭代遍历（不用递归，避免超深栈撑爆解释器递归限制）。"""
        stack = [self.root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(node.children.values())

    def func_stats(self) -> dict[str, tuple[int, int]]:
        """按函数名汇总 {func: (self_count, total_count)}，用于与全量统计对拍。"""
        stats: dict[str, list[int]] = {}
        for node in self.iter_nodes():
            if node is self.root:
                continue
            entry = stats.setdefault(node.key.func, [0, 0])
            entry[0] += node.self_count
            entry[1] += node.total_count
        return {k: (v[0], v[1]) for k, v in stats.items()}

    def lines(self) -> list[str]:
        """文本化树形报告（迭代实现）。每行：自身% 累计% 命中数。"""
        out: list[str] = []
        if self.samples == 0:
            return ["(no samples)"]
        header = f"{'self%':>7} {'total%':>7} {'hits':>7}  function"
        out.append(header)
        # 显式栈：(node, depth)，倒序压栈保证按 total 降序深度优先输出
        stack: list[tuple[Node, int]] = [(self.root, -1)]
        while stack:
            node, depth = stack.pop()
            if node is not self.root:
                self_pct = 100.0 * node.self_count / self.samples
                total_pct = 100.0 * node.total_count / self.samples
                out.append(
                    f"{self_pct:7.2f} {total_pct:7.2f} {node.total_count:7d}  "
                    f"{'  ' * depth}{node.key.label()}"
                )
            children = sorted(
                node.children.values(), key=lambda c: c.total_count, reverse=True
            )
            for child in reversed(children):
                stack.append((child, depth + 1))
        return out


class StackSampler:
    """固定频率调用栈采样器。

    用法：
        sampler = StackSampler(interval=0.001)  # 1kHz
        sampler.start()
        ...被测代码（主线程）...
        sampler.stop()
        print("\\n".join(sampler.tree.lines()))
        print(sampler.loss_report())
    """

    _CLOCKS = {
        "real": (signal.ITIMER_REAL, signal.SIGALRM, time.monotonic),
        "prof": (signal.ITIMER_PROF, signal.SIGPROF, time.process_time),
    }

    def __init__(self, interval: float = 0.001, clock: str = "real"):
        if interval <= 0:
            raise ValueError("interval must be positive")
        if clock not in self._CLOCKS:
            raise ValueError(f"clock must be one of {sorted(self._CLOCKS)}")
        self.interval = interval
        self.clock = clock
        self._itimer, self._signum, self._ticker = self._CLOCKS[clock]
        self.tree = StackTree()
        self.missed_reentrant = 0  # 处理器重入（上一次还没处理完）导致的丢失
        self.missed_noframe = 0    # 抓不到目标线程帧导致的丢失
        self._busy = False
        self._tid = threading.main_thread().ident
        self._t_start: float | None = None
        self._t_end: float | None = None
        self._old_handler = None

    def _handler(self, signum, frame) -> None:
        if self._busy:
            self.missed_reentrant += 1
            return
        self._busy = True
        try:
            fr = sys._current_frames().get(self._tid)
            if fr is None:
                self.missed_noframe += 1
                return
            keys: list[FrameKey] = []
            while fr is not None:
                code = fr.f_code
                keys.append(FrameKey(code.co_name, code.co_filename, code.co_firstlineno))
                fr = fr.f_back
            keys.reverse()
            # 信号处理器运行在主线程上，栈顶会带上本模块的帧，剥掉
            while keys and keys[-1].filename == _THIS_FILE:
                keys.pop()
            if not keys:
                self.missed_noframe += 1
                return
            self.tree.add(fold_frames(keys))
        finally:
            self._busy = False

    def start(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("StackSampler 只能在主线程启动（信号限制）")
        self._t_start = self._ticker()
        self._old_handler = signal.signal(self._signum, self._handler)
        signal.setitimer(self._itimer, self.interval, self.interval)

    def stop(self) -> None:
        signal.setitimer(self._itimer, 0.0, 0.0)
        if self._old_handler is not None:
            signal.signal(self._signum, self._old_handler)
            self._old_handler = None
        self._t_end = self._ticker()

    def __enter__(self) -> "StackSampler":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    @property
    def expected_samples(self) -> int:
        """按计时器耗时 / 采样间隔估算的理论采样数。"""
        if self._t_start is None:
            return 0
        end = self._t_end if self._t_end is not None else self._ticker()
        return int((end - self._t_start) / self.interval)

    def loss_report(self) -> dict:
        """采样丢失统计：期望数、实采数、估算丢失、丢失原因细分。"""
        taken = self.tree.samples
        expected = self.expected_samples
        lost = max(0, expected - taken)
        return {
            "clock": self.clock,
            "interval_ms": self.interval * 1000.0,
            "expected": expected,
            "taken": taken,
            "lost_estimate": lost,
            "lost_pct": (100.0 * lost / expected) if expected else 0.0,
            "missed_reentrant": self.missed_reentrant,
            "missed_noframe": self.missed_noframe,
        }
