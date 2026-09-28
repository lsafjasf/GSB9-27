"""模板归并日志抑制器（仅标准库）。

核心语义：
- 相同模板（参数归一化后）的日志归并为一组。
- 模板首次出现：立即全量放行，绝不被抑制窗口吞掉。
- 窗口期内的重复：计数 + 采样参数，不写出。
- 窗口到期：输出一条汇总「过去 N 秒重复 M 次」+ 参数分布摘要。
- 模板数量有硬上限，超出按 LRU 淘汰；淘汰前冲刷未发出的汇总，不丢信息。
"""

from __future__ import annotations

import re
import time
from collections import Counter, OrderedDict

_PLACEHOLDER = "*"

# 归一化规则：把"参数"替换成占位符。顺序敏感，先长后短。
_NORMALIZERS = (
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),  # UUID
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"),        # IP[:port]
    re.compile(r"\b0x[0-9a-fA-F]+\b"),                          # 十六进制
    re.compile(r"\b[0-9a-fA-F]{16,}\b"),                        # 长 hex / hash
    re.compile(r'"[^"\n]*"'),                                   # 双引号串
    re.compile(r"'[^'\n]*'"),                                   # 单引号串
    re.compile(r"(?<![\w.])\d+(?:\.\d+)?"),                      # 数字（含 30s 这类）
)


def extract_template(message: str) -> tuple[str, tuple[str, ...]]:
    """把一条日志归一化为 (模板, 参数列表)。"""
    params: list[str] = []

    def _sub(match: re.Match) -> str:
        params.append(match.group(0))
        return _PLACEHOLDER

    template = message
    for pattern in _NORMALIZERS:
        template = pattern.sub(_sub, template)
    return template, tuple(params)


class _TemplateState:
    __slots__ = ("window_start", "suppressed", "samples", "distinct_values", "_max_samples", "_max_distinct")

    def __init__(self, now: float, max_samples: int, max_distinct: int) -> None:
        self.window_start = now
        self.suppressed = 0
        self.samples: list[tuple[str, ...]] = []
        self.distinct_values: list[set[str]] = []
        self._max_samples = max_samples
        self._max_distinct = max_distinct

    def record(self, params: tuple[str, ...]) -> None:
        self.suppressed += 1
        if len(self.samples) < self._max_samples:
            self.samples.append(params)
        # 每个参数位置维护一个有界 distinct 集合，用于分布摘要
        for idx, value in enumerate(params):
            while idx >= len(self.distinct_values):
                self.distinct_values.append(set())
            bucket = self.distinct_values[idx]
            if len(bucket) < self._max_distinct:
                bucket.add(value)

    def param_summary(self, top: int = 3) -> str:
        if not self.samples:
            return "无样本"
        width = max(len(sample) for sample in self.samples)
        parts = []
        for idx in range(width):
            counter: Counter[str] = Counter()
            for sample in self.samples:
                if idx < len(sample):
                    counter[sample[idx]] += 1
            top_items = ", ".join(f"{value}x{count}" for value, count in counter.most_common(top))
            distinct = len(self.distinct_values[idx]) if idx < len(self.distinct_values) else 0
            parts.append(f"arg{idx}=[{top_items}](distinct~{distinct})")
        return "; ".join(parts)


class LogSuppressor:
    """按模板归并的日志抑制器。

    :param interval: 汇总间隔（秒）。窗口内重复只计数不输出。
    :param max_templates: 模板状态硬上限，超出按 LRU 淘汰（淘汰前冲刷汇总）。
    :param max_samples: 每个模板最多保留的参数样本数（用于分布摘要）。
    :param max_distinct: 每个参数位置最多跟踪的 distinct 值个数。
    :param time_fn: 时钟，可注入便于测试。
    """

    def __init__(
        self,
        interval: float = 10.0,
        max_templates: int = 10_000,
        max_samples: int = 32,
        max_distinct: int = 64,
        time_fn=time.monotonic,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval 必须为正数")
        if max_templates < 1:
            raise ValueError("max_templates 必须 >= 1")
        self.interval = interval
        self.max_templates = max_templates
        self.max_samples = max_samples
        self.max_distinct = max_distinct
        self._time = time_fn
        self._states: OrderedDict[str, _TemplateState] = OrderedDict()
        # 统计指标
        self.received = 0
        self.emitted_raw = 0
        self.emitted_summary = 0
        self.suppressed_total = 0
        self.evicted = 0
        self.peak_templates = 0

    @property
    def tracked_templates(self) -> int:
        return len(self._states)

    def process(self, message: str, now: float | None = None) -> list[str]:
        """处理一条日志，返回需要写出的行（0~N 行）。"""
        now = self._time() if now is None else now
        self.received += 1
        template, params = extract_template(message)
        outputs: list[str] = []

        state = self._states.get(template)
        if state is None:
            # 新模板：立即全量放行
            outputs.append(message)
            self.emitted_raw += 1
            self._states[template] = _TemplateState(now, self.max_samples, self.max_distinct)
            self._evict_if_needed(outputs, now)
            self.peak_templates = max(self.peak_templates, len(self._states))
            return outputs

        self._states.move_to_end(template)
        elapsed = now - state.window_start
        if elapsed < self.interval:
            # 窗口内：抑制
            state.record(params)
            self.suppressed_total += 1
            return outputs

        # 窗口到期：先冲刷上一个窗口的汇总，本条作为新窗口首条全量放行
        if state.suppressed > 0:
            outputs.append(self._format_summary(template, state, now))
            self.emitted_summary += 1
        outputs.append(message)
        self.emitted_raw += 1
        self._states[template] = _TemplateState(now, self.max_samples, self.max_distinct)
        return outputs

    def flush(self, now: float | None = None) -> list[str]:
        """冲刷所有未发出的汇总（例如进程退出前调用）。"""
        now = self._time() if now is None else now
        outputs = []
        for template, state in self._states.items():
            if state.suppressed > 0:
                outputs.append(self._format_summary(template, state, now))
                self.emitted_summary += 1
        self._states.clear()
        return outputs

    def _format_summary(self, template: str, state: _TemplateState, now: float) -> str:
        elapsed = now - state.window_start
        return (
            f"[SUPPRESSED] 过去 {elapsed:.1f} 秒重复 {state.suppressed} 次 "
            f"| template={template!r} | 参数分布: {state.param_summary()}"
        )

    def _evict_if_needed(self, outputs: list[str], now: float) -> None:
        while len(self._states) > self.max_templates:
            template, state = self._states.popitem(last=False)  # LRU 淘汰
            self.evicted += 1
            if state.suppressed > 0:
                outputs.append(self._format_summary(template, state, now))
                self.emitted_summary += 1
