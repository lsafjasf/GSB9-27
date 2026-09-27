"""Snowflake 风格趋势递增唯一 ID 生成器（仅标准库）。

ID 结构（64 bit）：
    [ 1 bit 符号位(恒0) | 41 bit 毫秒时间戳(相对自定义纪元) | 10 bit 机器号 | 12 bit 序列号 ]

- 41 bit 时间戳：自 EPOCH 起约 69 年可用。
- 10 bit 机器号：0..1023，部署时按机器分配；机器被复制时必须分配不同 machine_id，
  否则同一毫秒同一序列下无法保证唯一（这是所有 snowflake 方案的固有约束）。
- 12 bit 序列号：同一毫秒内 0..4095，耗尽后【确定行为：自旋等待下一毫秒】。

时钟回拨策略：
- 回拨幅度 <= max_backward_ms（默认 5ms）：视为时钟抖动，自旋等待时钟追平。
- 回拨幅度 >  max_backward_ms：抛出 ClockMovedBackwardsError，携带 offset_ms 偏差值，
  绝不产出重复或倒退的 ID。
"""

from __future__ import annotations

import threading
import time

EPOCH = 1704067200000  # 2024-01-01T00:00:00Z，自定义纪元（毫秒）

TIMESTAMP_BITS = 41
MACHINE_BITS = 10
SEQUENCE_BITS = 12

MAX_MACHINE_ID = (1 << MACHINE_BITS) - 1          # 1023
MAX_SEQUENCE = (1 << SEQUENCE_BITS) - 1           # 4095
MAX_TIMESTAMP = (1 << TIMESTAMP_BITS) - 1

MACHINE_SHIFT = SEQUENCE_BITS
TIMESTAMP_SHIFT = SEQUENCE_BITS + MACHINE_BITS


class ClockMovedBackwardsError(RuntimeError):
    """时钟大幅回拨，拒绝生成 ID。offset_ms 为回拨偏差（毫秒）。"""

    def __init__(self, offset_ms: int):
        self.offset_ms = offset_ms
        super().__init__(
            f"clock moved backwards by {offset_ms}ms; refusing to generate id"
        )


class SequenceOverflowError(RuntimeError):
    """保留异常：当前实现选择等待下一毫秒，不会抛出。"""


def _system_clock_ms() -> int:
    return time.time_ns() // 1_000_000


class SnowflakeGenerator:
    """线程安全的趋势递增唯一 ID 生成器。

    :param machine_id: 机器号，0..1023。
    :param clock: 可注入时钟，返回 Unix 毫秒时间戳；默认系统时钟。
    :param epoch: 自定义纪元（毫秒），需与集群内所有节点一致。
    :param max_backward_ms: 容忍的最大时钟回拨幅度，超过则抛 ClockMovedBackwardsError。
    """

    def __init__(
        self,
        machine_id: int,
        clock=_system_clock_ms,
        epoch: int = EPOCH,
        max_backward_ms: int = 5,
    ):
        if not isinstance(machine_id, int) or isinstance(machine_id, bool):
            raise ValueError(f"machine_id must be an int, got {machine_id!r}")
        if not 0 <= machine_id <= MAX_MACHINE_ID:
            raise ValueError(
                f"machine_id must be in [0, {MAX_MACHINE_ID}], got {machine_id}"
            )
        if max_backward_ms < 0:
            raise ValueError("max_backward_ms must be >= 0")

        self.machine_id = machine_id
        self.clock = clock
        self.epoch = epoch
        self.max_backward_ms = max_backward_ms

        self._lock = threading.Lock()
        self._last_timestamp = -1
        self._sequence = 0

    def _wait_next_ms(self, last: int) -> int:
        """自旋直到时钟超过 last（相对纪元的毫秒）。"""
        while True:
            now = self.clock() - self.epoch
            if now > last:
                return now
            time.sleep(0)  # 让出 GIL，避免忙等饿死其他线程

    def next_id(self) -> int:
        with self._lock:
            timestamp = self.clock() - self.epoch

            if timestamp < self._last_timestamp:
                offset = self._last_timestamp - timestamp
                if offset <= self.max_backward_ms:
                    # 小幅回拨：等待时钟追平上次时间戳
                    timestamp = self._wait_next_ms(self._last_timestamp)
                else:
                    # 大幅回拨：拒绝生成并报告偏差
                    raise ClockMovedBackwardsError(offset)

            if timestamp == self._last_timestamp:
                self._sequence = (self._sequence + 1) & MAX_SEQUENCE
                if self._sequence == 0:
                    # 序列耗尽：确定行为 = 等待下一毫秒
                    timestamp = self._wait_next_ms(timestamp)
            else:
                self._sequence = 0

            if timestamp > MAX_TIMESTAMP:
                raise OverflowError("timestamp exceeds 41 bits; epoch exhausted")

            self._last_timestamp = timestamp
            return (
                (timestamp << TIMESTAMP_SHIFT)
                | (self.machine_id << MACHINE_SHIFT)
                | self._sequence
            )

    # ---- 解析辅助（便于测试与排障） ----
    @staticmethod
    def parse(id_value: int) -> dict:
        return {
            "timestamp_ms": (id_value >> TIMESTAMP_SHIFT) + EPOCH,
            "machine_id": (id_value >> MACHINE_SHIFT) & MAX_MACHINE_ID,
            "sequence": id_value & MAX_SEQUENCE,
        }
