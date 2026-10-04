"""事件时间滚动窗口切分与迟到数据处理（仅标准库）。

边界规则
--------
- 滚动窗口大小固定为 ``window_size`` 秒，窗口以 UTC 纪元起点
  1970-01-01T00:00:00Z 为锚点对齐。
- 每个窗口为 **左闭右开** 区间 ``[window_start, window_end)``：
  * 事件时间恰好等于窗口起点 -> 属于该窗口；
  * 事件时间恰好等于窗口终点 -> 属于下一个窗口（不算本窗口）。
- 所有带时区偏移的时间戳先归一化为 UTC 再切窗，因此“同一时刻、不同
  时区写法”的事件一定落在同一个窗口里。

迟到规则
--------
- 水位线（watermark）= 已处理事件中最大的事件时间。
- 事件时间 < 当前水位线 => 迟到数据。
- 事件时间 >= 水位线 - allowed_lateness => 允许迟到：补算进所属窗口，
  窗口结果被更新（保留更新前后的快照）。
- 事件时间 <  水位线 - allowed_lateness => 严重迟到：不参与计算，
  单独记录到 rejected 列表（绝不静默丢弃）。
  边界 ``event_time == watermark - allowed_lateness`` 按允许迟到处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass(frozen=True)
class Window:
    """左闭右开窗口 [start_epoch, end_epoch)，时间为 UTC epoch 秒。"""

    start_epoch: int
    end_epoch: int

    def contains(self, epoch_seconds: float) -> bool:
        return self.start_epoch <= epoch_seconds < self.end_epoch

    def start_iso(self) -> str:
        return _format_epoch(self.start_epoch)

    def end_iso(self) -> str:
        return _format_epoch(self.end_epoch)

    def __str__(self) -> str:
        return f"[{self.start_iso()}, {self.end_iso()})"


@dataclass
class UpdateRecord:
    """一次窗口更新的前后快照，用于展示迟到补算效果。"""

    seq: int
    event_id: Any
    event_time_iso: str
    window: Window
    late: bool
    lateness_seconds: float
    before: dict
    after: dict


@dataclass
class RejectedRecord:
    """超出允许范围的迟到事件（严重迟到），单独留档。"""

    event_id: Any
    event_time_iso: str
    event_epoch: float
    watermark_epoch: float
    allowed_lateness_seconds: int
    lateness_seconds: float
    window: Window
    value: Any
    reason: str


def parse_event_time(value: Any) -> datetime:
    """把 ISO-8601 字符串 / datetime / epoch 秒统一转成带时区的 UTC datetime。"""

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    else:
        raise TypeError(f"不支持的事件时间类型: {type(value)!r}")

    if dt.tzinfo is None:
        raise ValueError(f"事件时间缺少时区信息: {value!r}（拒绝猜测时区）")
    return dt.astimezone(timezone.utc)


def assign_window(event_time: Any, window_size_seconds: int) -> Window:
    """按事件时间把一条数据分配到左闭右开的滚动窗口。"""

    if window_size_seconds <= 0:
        raise ValueError("window_size_seconds 必须为正整数")
    epoch = parse_event_time(event_time).timestamp()
    start = int(epoch // window_size_seconds) * window_size_seconds
    return Window(start, start + window_size_seconds)


def _format_epoch(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class WindowedAggregator:
    """事件时间滚动窗口聚合器（批处理按到达顺序逐条摄入）。"""

    def __init__(self, window_size_seconds: int, allowed_lateness_seconds: int = 0):
        if allowed_lateness_seconds < 0:
            raise ValueError("allowed_lateness_seconds 不能为负")
        self.window_size = window_size_seconds
        self.allowed_lateness = allowed_lateness_seconds
        self.windows: dict[int, dict] = {}
        self.watermark: Optional[float] = None
        self.updates: list[UpdateRecord] = []
        self.rejected: list[RejectedRecord] = []
        self.assignments: list[dict] = []
        self._seq = 0

    def add(self, event: dict) -> Any:
        """摄入一条事件 ``{"id", "event_time", "value"}``。

        返回：
        - ``UpdateRecord``：事件被接受（准时或允许范围内迟到）；
        - ``RejectedRecord``：严重迟到，被单独记录。
        """

        event_id = event.get("id")
        dt = parse_event_time(event["event_time"])
        epoch = dt.timestamp()
        value = event["value"]
        window = assign_window(dt, self.window_size)

        if self.watermark is None:
            self.watermark = epoch
        elif epoch > self.watermark:
            self.watermark = epoch

        lateness = (self.watermark - epoch) if epoch < self.watermark else 0.0
        is_late = lateness > 0
        too_late = epoch < self.watermark - self.allowed_lateness

        self.assignments.append(
            {
                "id": event_id,
                "event_time": _format_epoch(epoch),
                "window": str(window),
                "value": value,
                "late": is_late,
                "lateness_seconds": lateness,
                "status": "rejected" if too_late else ("late" if is_late else "on_time"),
            }
        )

        if too_late:
            record = RejectedRecord(
                event_id=event_id,
                event_time_iso=_format_epoch(epoch),
                event_epoch=epoch,
                watermark_epoch=self.watermark,
                allowed_lateness_seconds=self.allowed_lateness,
                lateness_seconds=lateness,
                window=window,
                value=value,
                reason=(
                    f"迟到 {lateness:.0f}s，超过允许迟到 "
                    f"{self.allowed_lateness}s（水位线 {_format_epoch(self.watermark)}）"
                ),
            )
            self.rejected.append(record)
            return record

        self._seq += 1
        before = dict(self.windows.get(window.start_epoch, {"count": 0, "sum": 0}))
        agg = self.windows.setdefault(window.start_epoch, {"count": 0, "sum": 0})
        agg["count"] += 1
        agg["sum"] += value
        after = dict(agg)

        update = UpdateRecord(
            seq=self._seq,
            event_id=event_id,
            event_time_iso=_format_epoch(epoch),
            window=window,
            late=is_late,
            lateness_seconds=lateness,
            before=before,
            after=after,
        )
        self.updates.append(update)
        return update

    def add_batch(self, events) -> list:
        return [self.add(event) for event in events]

    def results(self) -> list[dict]:
        """按窗口起点排序的最终结果。"""

        return [
            {
                "window": str(Window(start, start + self.window_size)),
                "start_epoch": start,
                **agg,
            }
            for start, agg in sorted(self.windows.items())
        ]

    def watermark_iso(self) -> Optional[str]:
        return _format_epoch(self.watermark) if self.watermark is not None else None
