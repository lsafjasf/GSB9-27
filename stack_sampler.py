"""调用栈采样与聚合库（仅标准库）。

组成：
- Node         : 聚合树节点（样本计数 + 全量计时两套字段）
- StackSampler : 固定频率采样目标线程调用栈，聚合为树
- FullTracer   : 基于 sys.setprofile 的全量调用统计（对拍基准）
- aggregate_by_key / format_tree : 汇总与格式化输出

递归折叠约定：同一条栈路径上，相邻的同名函数帧只保留一个节点。
即 f -> f -> f -> g 折叠为 f -> g。这样递归不会让同一函数在树里
变成一串不同节点，自身/累计耗时语义与全量统计保持一致。

采样后端：
- "signal"（默认，仅主线程）：signal.setitimer(ITIMER_REAL) + SIGALRM
  处理器在目标线程自身内执行采样，不受 GIL 饥饿影响（后台线程方案在
  目标线程忙等时抢不到 GIL，默认 5ms 切换间隔会丢掉大量样本）。
- "thread"：后台线程 + sys._current_frames()，可采样任意线程，
  但目标线程长时间持有 GIL 时样本会延迟到达。

采样丢失处理：
- signal 后端：信号不排队，通过相邻两次采样的实际间隔推算丢失 tick 数。
- thread 后端：按绝对时间表推进，醒晚超过半个周期记 missed 并重对齐；
  目标帧不可用记 dropped。
- loss_report() 输出 expected/taken/missed/dropped/loss_rate。
"""

import random
import signal
import sys
import threading
import time

__all__ = [
    "Node",
    "StackSampler",
    "FullTracer",
    "aggregate_by_key",
    "format_tree",
    "frame_qualname",
]


def frame_qualname(frame):
    code = frame.f_code
    return getattr(code, "co_qualname", None) or code.co_name


class Node:
    __slots__ = (
        "key",
        "self_count",   # 采样：以本节点为栈顶的样本数
        "total_count",  # 采样：经过本节点的样本数
        "self_time",    # 全量：自身耗时（秒）
        "total_time",   # 全量：累计耗时（秒，递归折叠后只计最外层）
        "calls",        # 全量：调用次数
        "children",
    )

    def __init__(self, key):
        self.key = key
        self.self_count = 0
        self.total_count = 0
        self.self_time = 0.0
        self.total_time = 0.0
        self.calls = 0
        self.children = {}


def fold_path(names):
    """把一条栈路径（外->内）中相邻重复帧折叠掉。"""
    folded = []
    for name in names:
        if not folded or folded[-1] != name:
            folded.append(name)
    return folded


class StackSampler:
    """固定频率的调用栈采样器。用法：

        with StackSampler(interval=0.001) as sampler:
            workload()
        print(format_tree(sampler.root, sampler.root.total_count))
        print(sampler.loss_report())
    """

    def __init__(self, interval=0.001, target_thread=None, backend=None,
                 max_depth=10000, jitter=0.0):
        self.interval = float(interval)
        # jitter in [0,1)：每个周期在实际间隔上叠加均匀随机扰动，
        # 避免采样周期与被测程序循环周期相位锁定（混叠）
        self.jitter = float(jitter)
        target = target_thread or threading.current_thread()
        self.target_ident = target.ident
        if backend is None:
            backend = (
                "signal"
                if target is threading.main_thread()
                and threading.current_thread() is threading.main_thread()
                else "thread"
            )
        if backend == "signal" and (
            threading.current_thread() is not threading.main_thread()
            or target is not threading.main_thread()
        ):
            raise ValueError("signal 后端只能在主线程采样主线程")
        self.backend = backend
        self.max_depth = max_depth
        self.root = Node("<root>")
        self.samples_taken = 0
        self.samples_missed = 0    # 跟不上的 tick 数
        self.samples_dropped = 0   # 目标帧不可用而丢弃的次数
        self.expected_samples = 0
        self._stop = threading.Event()
        self._thread = None
        self._last_tick = None
        self._cur_interval = self.interval
        self._prev_handler = None
        self._prev_timer = None
        self._in_handler = False

    # ---- 生命周期 ----

    def start(self):
        if self.backend == "signal":
            self._prev_handler = signal.signal(signal.SIGALRM, self._on_alarm)
            if self.jitter:
                self._cur_interval = self._next_interval()
                self._prev_timer = signal.setitimer(
                    signal.ITIMER_REAL, self._cur_interval, 0
                )
            else:
                self._cur_interval = self.interval
                self._prev_timer = signal.setitimer(
                    signal.ITIMER_REAL, self.interval, self.interval
                )
        else:
            if self._thread is not None:
                raise RuntimeError("already started")
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="StackSampler", daemon=True
            )
            self._thread.start()
        return self

    def stop(self):
        if self.backend == "signal":
            signal.setitimer(signal.ITIMER_REAL, 0)
            if self._prev_handler is not None:
                signal.signal(signal.SIGALRM, self._prev_handler)
                self._prev_handler = None
        else:
            self._stop.set()
            if self._thread is not None:
                self._thread.join()
                self._thread = None
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # ---- signal 后端 ----

    def _on_alarm(self, signum, frame):
        if self._in_handler:
            # 处理器尚未返回时再次触发（间隔小于处理耗时）：记丢失
            self.samples_missed += 1
            return
        self._in_handler = True
        try:
            self._on_alarm_inner(frame)
        finally:
            self._in_handler = False

    def _next_interval(self):
        if not self.jitter:
            return self.interval
        return self.interval * random.uniform(
            1.0 - self.jitter, 1.0 + self.jitter
        )

    def _on_alarm_inner(self, frame):
        now = time.perf_counter()
        if self._last_tick is not None:
            gap = now - self._last_tick
            if gap > self._cur_interval * 1.5:
                # 信号不排队：按间隔推算中间丢了多少 tick
                self.samples_missed += max(0, round(gap / self._cur_interval) - 1)
        self._last_tick = now
        self.expected_samples += 1
        # 信号处理器第二参数即被中断的帧，直接用它，
        # 避免把处理器自身的帧采进栈里
        self._sample_once(frame)
        if self.jitter:
            self._cur_interval = self._next_interval()
            signal.setitimer(signal.ITIMER_REAL, self._cur_interval, 0)

    # ---- thread 后端 ----

    def _run(self):
        interval = self.interval
        next_tick = time.perf_counter()
        while not self._stop.is_set():
            step = self._next_interval() if self.jitter else interval
            next_tick += step
            delay = next_tick - time.perf_counter()
            if delay > 0:
                self._stop.wait(delay)
            if self._stop.is_set():
                break
            now = time.perf_counter()
            if now - next_tick > step * 0.5:
                self.samples_missed += 1
                next_tick = now
                continue
            self.expected_samples += 1
            self._sample_once()

    # ---- 采样与聚合 ----

    def _sample_once(self, frame=None):
        if frame is None:
            frame = sys._current_frames().get(self.target_ident)
        if frame is None:
            self.samples_dropped += 1
            return
        path = []
        while frame is not None and len(path) < self.max_depth:
            path.append(frame_qualname(frame))
            frame = frame.f_back
        path.reverse()
        node = self.root
        node.total_count += 1
        for name in fold_path(path):
            child = node.children.get(name)
            if child is None:
                child = node.children[name] = Node(name)
            child.total_count += 1
            node = child
        node.self_count += 1
        self.samples_taken += 1

    def loss_report(self):
        total_ticks = self.expected_samples + self.samples_missed
        loss = (
            (self.samples_missed + self.samples_dropped) / total_ticks
            if total_ticks
            else 0.0
        )
        return {
            "backend": self.backend,
            "interval": self.interval,
            "expected": self.expected_samples,
            "taken": self.samples_taken,
            "missed_ticks": self.samples_missed,
            "dropped": self.samples_dropped,
            "loss_rate": loss,
        }


class FullTracer:
    """基于 sys.setprofile 的全量统计，作为采样结果的对拍基准。

    递归折叠：被调函数与当前栈顶节点同名时，复用同一树节点。
    计时栈上的每一项记录 [node, start, child_time, is_outer]：
    - self_time  : 每一项都累加 elapsed - child_time，折叠项相加后
                   恰好等于"该函数总时间减去非自身后代时间"。
    - total_time : 只有最外层那一项累加，避免递归重复计累计耗时。
    """

    def __init__(self, trace_c_calls=False):
        self.root = Node("<root>")
        self.trace_c_calls = trace_c_calls
        self._stack = []  # [node, start, child_time, is_outer]

    def _on_call(self, name, now):
        if self._stack:
            parent_node = self._stack[-1][0]
            if parent_node.key == name:
                node = parent_node
                is_outer = False
            else:
                node = parent_node.children.get(name)
                if node is None:
                    node = parent_node.children[name] = Node(name)
                is_outer = True
        else:
            node = self.root.children.get(name)
            if node is None:
                node = self.root.children[name] = Node(name)
            is_outer = True
        node.calls += 1
        self._stack.append([node, now, 0.0, is_outer])

    def _on_return(self, now):
        if not self._stack:
            return
        node, start, child_time, is_outer = self._stack.pop()
        elapsed = now - start
        node.self_time += elapsed - child_time
        if is_outer:
            node.total_time += elapsed
        if self._stack:
            self._stack[-1][2] += elapsed
        else:
            self.root.total_time += elapsed

    def _trace(self, frame, event, arg):
        now = time.perf_counter()
        if event == "call":
            self._on_call(frame_qualname(frame), now)
        elif event in ("return", "exception"):
            self._on_return(now)
        elif self.trace_c_calls and event == "c_call":
            name = getattr(arg, "__qualname__", None) or getattr(
                arg, "__name__", repr(arg)
            )
            self._on_call(name, now)
        elif self.trace_c_calls and event in ("c_return", "c_exception"):
            self._on_return(now)
        return self._trace

    def start(self):
        sys.setprofile(self._trace)
        return self

    def stop(self):
        sys.setprofile(None)
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def aggregate_by_key(root, mode="count"):
    """把树按函数名汇总：key -> [self, total]。

    mode="count" 用采样计数，mode="time" 用全量计时。
    递归折叠保证同一函数在任一路径上最多出现一次，因此跨节点求和
    就是"该函数在栈上"的总量。
    """
    agg = {}

    def visit(node):
        entry = agg.setdefault(node.key, [0.0, 0.0])
        if mode == "count":
            entry[0] += node.self_count
            entry[1] += node.total_count
        else:
            entry[0] += node.self_time
            entry[1] += node.total_time
        for child in node.children.values():
            visit(child)

    visit(root)
    return agg


def format_tree(root, total, mode="count", min_pct=0.5, indent="  "):
    """把聚合树格式化为文本，输出每个节点的自身/累计占比。"""
    lines = [f"{'cum%':>7} {'self%':>7}  node"]

    def visit(node, depth):
        if mode == "count":
            cum = node.total_count / total * 100 if total else 0.0
            self_pct = node.self_count / total * 100 if total else 0.0
        else:
            cum = node.total_time / total * 100 if total else 0.0
            self_pct = node.self_time / total * 100 if total else 0.0
        if depth > 0 and cum < min_pct:
            return
        lines.append(f"{cum:6.2f}% {self_pct:6.2f}%  {indent * depth}{node.key}")
        for child in sorted(
            node.children.values(),
            key=lambda c: (c.total_count if mode == "count" else c.total_time),
            reverse=True,
        ):
            visit(child, depth + 1)

    visit(root, 0)
    return "\n".join(lines)
