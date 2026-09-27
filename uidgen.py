"""单机趋势递增、无重复的 64 位数值标识生成器（仅标准库）。

位布局（从高到低，共 64 位，最高位恒为 0，保证标识为非负整数）::

    1 位符号位(0) | timestamp_bits 位毫秒时间戳 | machine_bits 位机器号 | 剩余位为毫秒内序列号

默认布局：41 位时间戳（相对自定义纪元，约 69 年）+ 5 位机器号（0..31）+ 17 位序列号
（每毫秒最多 131072 个标识）。同一毫秒内序列耗尽时的确定行为：**自旋等待下一毫秒**
（不报错），等待期间若检测到回拨超限仍会拒绝。

时钟回拨策略（见 README「回拨处理说明」）：
- 小幅回拨（偏差 <= max_backward_ms）：阻塞等待时钟追平上次发号时刻，期间不发号；
- 大幅回拨（偏差 >  max_backward_ms）：抛出 ClockRolledBackError 并携带偏差值，
  绝不产出一批重复或倒退的标识。

线程安全：next_id() 可在多线程下并发调用，内部以锁保证状态推进的原子性。

机器复制说明：本库只能保证「同一 machine_id 的单个进程」内标识唯一。机器被复制
（虚拟机克隆、容器多副本）时，必须为每个副本注入不同的 machine_id（可用
IdGenerator.from_env() 从环境变量读取），否则不同副本可能产出相同标识——这属于
部署约束，库无法在不引入外部协调的前提下自动消除。
"""

import os
import threading
import time

__all__ = [
    "DEFAULT_EPOCH_MS",
    "MACHINE_ID_ENV_VAR",
    "ClockRolledBackError",
    "IdGenerator",
    "IdGeneratorError",
    "InvalidConfigError",
    "TimestampOverflowError",
]

_TOTAL_BITS = 64
_USABLE_BITS = _TOTAL_BITS - 1  # 最高位固定为 0，保证标识非负

DEFAULT_TIMESTAMP_BITS = 41
DEFAULT_MACHINE_BITS = 5
DEFAULT_EPOCH_MS = 1577836800000  # 2020-01-01T00:00:00Z，自定义纪元（毫秒）
DEFAULT_MAX_BACKWARD_MS = 5       # 允许等待追平的最大回拨幅度（毫秒）
DEFAULT_POLL_INTERVAL_MS = 1      # 等待时钟时的轮询间隔（毫秒）

MACHINE_ID_ENV_VAR = "UIDGEN_MACHINE_ID"


def _system_clock_ms():
    """默认时钟源：Unix 毫秒时间戳。"""
    return int(time.time() * 1000)


def _system_sleep_ms(milliseconds):
    """默认睡眠函数：按毫秒睡眠。"""
    time.sleep(milliseconds / 1000.0)


class IdGeneratorError(Exception):
    """本库所有异常的基类。"""


class InvalidConfigError(IdGeneratorError, ValueError):
    """构造参数非法（如机器位配置非法、机器号越界）。"""


class TimestampOverflowError(IdGeneratorError):
    """时钟读数超出时间戳位可表示的范围。"""


class ClockRolledBackError(IdGeneratorError):
    """时钟回拨幅度超过容忍上限，拒绝生成标识。

    属性:
        last_ms:     最后一次发号所用的毫秒时间戳。
        now_ms:      触发拒绝时的时钟读数。
        backward_ms: 回拨偏差值（last_ms - now_ms，正数，毫秒）。
    """

    def __init__(self, last_ms, now_ms):
        self.last_ms = last_ms
        self.now_ms = now_ms
        self.backward_ms = last_ms - now_ms
        super().__init__(
            "clock moved backward by %d ms (last=%d, now=%d); "
            "refusing to generate id" % (self.backward_ms, last_ms, now_ms)
        )


def _require_non_negative_int(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidConfigError("%s must be a non-negative int, got %r" % (name, value))


class IdGenerator:
    """趋势递增、无重复的 64 位数值标识生成器。

    参数:
        machine_id:        机器号，范围 [0, 2**machine_bits - 1]。
        timestamp_bits:    时间戳位宽，默认 41。
        machine_bits:      机器位宽，默认 5；序列位宽 = 63 - 两者之和，必须 >= 1。
        epoch_ms:          自定义纪元（Unix 毫秒），时钟读数不得早于它。
        max_backward_ms:   允许等待追平的最大回拨幅度，超过则抛 ClockRolledBackError。
        clock_ms:          可注入的时钟源，返回 Unix 毫秒（默认取系统时间）。
        sleep_ms:          可注入的睡眠函数（默认 time.sleep），便于测试假时钟。
        poll_interval_ms:  等待时钟时的轮询间隔。
    """

    def __init__(
        self,
        machine_id,
        timestamp_bits=DEFAULT_TIMESTAMP_BITS,
        machine_bits=DEFAULT_MACHINE_BITS,
        epoch_ms=DEFAULT_EPOCH_MS,
        max_backward_ms=DEFAULT_MAX_BACKWARD_MS,
        clock_ms=None,
        sleep_ms=None,
        poll_interval_ms=DEFAULT_POLL_INTERVAL_MS,
    ):
        _require_non_negative_int("machine_id", machine_id)
        _require_non_negative_int("timestamp_bits", timestamp_bits)
        _require_non_negative_int("machine_bits", machine_bits)
        _require_non_negative_int("epoch_ms", epoch_ms)
        _require_non_negative_int("max_backward_ms", max_backward_ms)
        if isinstance(poll_interval_ms, bool) or not isinstance(poll_interval_ms, (int, float)) or poll_interval_ms <= 0:
            raise InvalidConfigError("poll_interval_ms must be a positive number, got %r" % (poll_interval_ms,))
        if timestamp_bits < 1:
            raise InvalidConfigError("timestamp_bits must be >= 1, got %d" % timestamp_bits)
        sequence_bits = _USABLE_BITS - timestamp_bits - machine_bits
        if sequence_bits < 1:
            raise InvalidConfigError(
                "timestamp_bits(%d) + machine_bits(%d) leaves no room for sequence bits (need >= 1)"
                % (timestamp_bits, machine_bits)
            )
        if machine_id >= (1 << machine_bits):
            raise InvalidConfigError(
                "machine_id %d does not fit in %d machine bits (max %d)"
                % (machine_id, machine_bits, (1 << machine_bits) - 1)
            )

        self._machine_id = machine_id
        self._timestamp_bits = timestamp_bits
        self._machine_bits = machine_bits
        self._sequence_bits = sequence_bits
        self._epoch_ms = epoch_ms
        self._max_backward_ms = max_backward_ms
        self._clock_ms = clock_ms if clock_ms is not None else _system_clock_ms
        self._sleep_ms = sleep_ms if sleep_ms is not None else _system_sleep_ms
        self._poll_interval_ms = poll_interval_ms

        self._machine_shift = sequence_bits
        self._timestamp_shift = sequence_bits + machine_bits
        self._max_sequence = (1 << sequence_bits) - 1
        self._max_timestamp = (1 << timestamp_bits) - 1

        self._lock = threading.Lock()
        self._last_timestamp_ms = -1  # 最后一次发号所用的时钟读数（毫秒）
        self._sequence = 0            # 该毫秒内已用的序列号

    @classmethod
    def from_env(cls, env_var=MACHINE_ID_ENV_VAR, **kwargs):
        """从环境变量读取机器号构造生成器（应对机器被复制的部署场景）。

        每个副本必须设置不同的环境变量值，例如 UIDGEN_MACHINE_ID=7。
        """
        raw = os.environ.get(env_var)
        if raw is None:
            raise InvalidConfigError(
                "environment variable %s is not set; every machine replica must be "
                "assigned a distinct machine id" % env_var
            )
        try:
            machine_id = int(raw, 10)
        except ValueError:
            raise InvalidConfigError(
                "environment variable %s=%r is not an integer" % (env_var, raw)
            ) from None
        return cls(machine_id, **kwargs)

    @property
    def machine_id(self):
        return self._machine_id

    @property
    def sequence_bits(self):
        return self._sequence_bits

    @property
    def max_sequence(self):
        """单毫秒内可分配的最大序列号（容量为 max_sequence + 1）。"""
        return self._max_sequence

    def next_id(self):
        """生成下一个标识。线程安全。

        同一毫秒内序列耗尽时阻塞等待下一毫秒；回拨超限抛 ClockRolledBackError；
        时钟读数早于纪元抛 ValueError；超出时间戳位宽抛 TimestampOverflowError。
        """
        with self._lock:
            now_ms = self._read_clock()
            if now_ms < self._last_timestamp_ms:
                now_ms = self._handle_backward(now_ms)
            if now_ms == self._last_timestamp_ms:
                if self._sequence >= self._max_sequence:
                    now_ms = self._wait_next_ms(now_ms)
                    self._sequence = 0
                else:
                    self._sequence += 1
            else:
                self._sequence = 0
            return self._compose(now_ms)

    def decode(self, identifier):
        """把标识解析为 (时间戳毫秒, 机器号, 序列号) 三元组，便于断言与排查。"""
        sequence = identifier & self._max_sequence
        machine = (identifier >> self._machine_shift) & ((1 << self._machine_bits) - 1)
        timestamp_ms = (identifier >> self._timestamp_shift) + self._epoch_ms
        return timestamp_ms, machine, sequence

    def _read_clock(self):
        now_ms = self._clock_ms()
        if now_ms < self._epoch_ms:
            raise ValueError(
                "clock reading %d ms is before epoch %d ms" % (now_ms, self._epoch_ms)
            )
        return now_ms

    def _compose(self, timestamp_ms):
        offset = timestamp_ms - self._epoch_ms
        if offset > self._max_timestamp:
            raise TimestampOverflowError(
                "timestamp %d ms exceeds %d timestamp bits (epoch=%d)"
                % (timestamp_ms, self._timestamp_bits, self._epoch_ms)
            )
        # 先校验后提交状态，溢出不会污染内部状态。
        self._last_timestamp_ms = timestamp_ms
        return (
            (offset << self._timestamp_shift)
            | (self._machine_id << self._machine_shift)
            | self._sequence
        )

    def _handle_backward(self, now_ms):
        """小幅回拨：阻塞等待时钟追平上次发号时刻，期间不发号。"""
        while True:
            if self._last_timestamp_ms - now_ms > self._max_backward_ms:
                raise ClockRolledBackError(self._last_timestamp_ms, now_ms)
            self._sleep_ms(self._poll_interval_ms)
            now_ms = self._read_clock()
            if now_ms >= self._last_timestamp_ms:
                return now_ms

    def _wait_next_ms(self, now_ms):
        """序列耗尽：阻塞等待时钟越过当前毫秒。"""
        while now_ms <= self._last_timestamp_ms:
            if self._last_timestamp_ms - now_ms > self._max_backward_ms:
                raise ClockRolledBackError(self._last_timestamp_ms, now_ms)
            self._sleep_ms(self._poll_interval_ms)
            now_ms = self._read_clock()
        return now_ms
