"""日志抑制器：按模板归并相同日志，首次全量保留，后续按间隔汇总。

仅依赖标准库。设计目标：
1. 相同模板（参数化后）的日志在窗口内只写一条汇总，含参数分布摘要；
2. 新模板立即放行，绝不被抑制窗口吞掉；
3. 抑制状态有硬上界（max_templates），LRU 淘汰并在淘汰前冲刷最终汇总，
   抑制器自身不会成为内存泄漏点。
"""

from __future__ import annotations

import re
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# 参数化规则：把易变部分替换为 {}，按出现顺序捕获参数。
_PARAM_PATTERNS = [
    re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),  # UUID
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"),                                          # IPv4[:port]
    re.compile(r"\b0x[0-9a-fA-F]+\b"),                                                             # hex
    re.compile(r'"[^"\n]{0,200}"'),                                                                # 双引号串
    re.compile(r"'[^'\n]{0,200}'"),                                                                # 单引号串
    re.compile(r"\b[a-zA-Z_][a-zA-Z_0-9]*\d+[a-zA-Z_0-9]*\b"),                                     # 含数字标识符 sda1/eth0
    re.compile(r"\b\d+(?:\.\d+)?(?:ms|us|ns|s|kb|mb|gb|tb|%)?\b"),                                 # 数字(可带单位)
]

OTHER_BUCKET = "<other>"


def extract_template(message: str) -> Tuple[str, Tuple[str, ...]]:
    """把一条日志归约为 (模板, 参数元组)。"""
    params: List[str] = []

    def _sub(match: re.Match) -> str:
        params.append(match.group(0))
        return "{}"

    template = message
    for pattern in _PARAM_PATTERNS:
        template = pattern.sub(_sub, template)
    return template, tuple(params)


@dataclass
class TemplateState:
    """单个模板的抑制状态（内存占用有界）。"""

    template: str
    first_message: str
    first_seen: float
    last_emit: float                      # 上次输出（首条或汇总）的时间
    suppressed: int = 0                   # 自上次输出以来被抑制的条数
    total_seen: int = 1
    # 每个参数位置的取值分布，distinct 取值数有上限，超出并入 <other>
    param_dist: List[Counter] = field(default_factory=list)
    max_distinct_per_param: int = 8

    def observe(self, params: Tuple[str, ...]) -> None:
        while len(self.param_dist) < len(params):
            self.param_dist.append(Counter())
        for idx, value in enumerate(params):
            dist = self.param_dist[idx]
            if value in dist:
                dist[value] += 1
            elif len(dist) < self.max_distinct_per_param:
                dist[value] += 1
            else:
                dist[OTHER_BUCKET] += 1

    def distribution_summary(self, top_k: int = 3) -> str:
        parts = []
        for idx, dist in enumerate(self.param_dist):
            if not dist:
                continue
            top = ", ".join(f"{v}×{c}" for v, c in dist.most_common(top_k))
            parts.append(f"p{idx}=[{top}]")
        return "; ".join(parts) if parts else "(无参数)"


class LogSuppressor:
    """模板归并抑制器。

    - interval 秒内同一模板的重复日志被抑制，到期输出一条汇总；
    - 新模板（含故障期间首次出现的错误模式）立即全量输出；
    - 模板数超过 max_templates 时按 LRU 淘汰最久未活动的模板，
      淘汰前若仍有未汇报的抑制计数，先冲刷一条最终汇总，不丢信息。
    """

    def __init__(
        self,
        interval: float = 10.0,
        max_templates: int = 10_000,
        max_distinct_per_param: int = 8,
        clock=time.monotonic,
    ) -> None:
        if max_templates < 1:
            raise ValueError("max_templates 必须 >= 1")
        self.interval = interval
        self.max_templates = max_templates
        self.max_distinct_per_param = max_distinct_per_param
        self._clock = clock
        self._states: "OrderedDict[str, TemplateState]" = OrderedDict()
        # 指标
        self.received = 0        # 输入总条数
        self.emitted = 0         # 输出总条数（首条 + 汇总）
        self.emitted_bytes_in = 0
        self.emitted_bytes_out = 0
        self.evicted = 0

    # ---- 内部 ----

    def _summary_line(self, state: TemplateState, now: float, final: bool = False) -> str:
        elapsed = now - state.last_emit
        tag = "SUPPRESSED-FINAL" if final else "SUPPRESSED"
        return (
            f"[{tag}] 过去 {elapsed:.1f} 秒重复 {state.suppressed} 次 | "
            f"模板: {state.template} | 参数分布: {state.distribution_summary()}"
        )

    def _emit(self, line: str, out: List[str]) -> None:
        out.append(line)
        self.emitted += 1
        self.emitted_bytes_out += len(line.encode("utf-8"))

    def _evict_lru(self, out: List[str], now: float) -> None:
        _, state = self._states.popitem(last=False)  # 最久未活动
        if state.suppressed > 0:
            self._emit(self._summary_line(state, now, final=True), out)
        self.evicted += 1

    # ---- 对外 ----

    def process(self, message: str, now: Optional[float] = None) -> List[str]:
        """处理一条日志，返回需要写盘的行（0 条 = 被抑制）。"""
        now = self._clock() if now is None else now
        self.received += 1
        self.emitted_bytes_in += len(message.encode("utf-8"))
        out: List[str] = []

        template, params = extract_template(message)
        state = self._states.get(template)

        if state is None:
            # 新模板：立即全量放行，不受任何窗口影响。
            if len(self._states) >= self.max_templates:
                self._evict_lru(out, now)
            state = TemplateState(
                template=template,
                first_message=message,
                first_seen=now,
                last_emit=now,
                max_distinct_per_param=self.max_distinct_per_param,
            )
            state.observe(params)
            self._states[template] = state
            self._emit(message, out)
            return out

        # 已有模板：归并抑制。
        self._states.move_to_end(template)
        state.total_seen += 1
        state.suppressed += 1
        state.observe(params)

        if now - state.last_emit >= self.interval:
            self._emit(self._summary_line(state, now), out)
            state.suppressed = 0
            state.last_emit = now
            for dist in state.param_dist:
                dist.clear()
        return out

    def flush(self, now: Optional[float] = None) -> List[str]:
        """收尾：把所有仍挂着未汇报抑制计数的模板各冲刷一条最终汇总。"""
        now = self._clock() if now is None else now
        out: List[str] = []
        for state in self._states.values():
            if state.suppressed > 0:
                self._emit(self._summary_line(state, now, final=True), out)
                state.suppressed = 0
                state.last_emit = now
        return out

    @property
    def tracked_templates(self) -> int:
        return len(self._states)

    def stats(self) -> dict:
        suppressed_total = self.received - self.emitted
        rate = suppressed_total / self.received if self.received else 0.0
        return {
            "received": self.received,
            "emitted": self.emitted,
            "suppressed": suppressed_total,
            "suppression_rate": rate,
            "bytes_in": self.emitted_bytes_in,
            "bytes_out": self.emitted_bytes_out,
            "byte_reduction": (
                1 - self.emitted_bytes_out / self.emitted_bytes_in
                if self.emitted_bytes_in else 0.0
            ),
            "tracked_templates": self.tracked_templates,
            "evicted": self.evicted,
        }
