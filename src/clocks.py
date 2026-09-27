"""统一时钟抽象。

两种时间语义：

- 墙上时间（wall time）：``Clock.wall()``，对应 ``time.time()``。
  表示日历时刻，可能因 NTP 校时、闰秒、人工改时间而回拨或跳变。
  适用场景：与外部世界对齐的绝对时刻语义，如证书/令牌过期时间、
  日志时间戳、定时任务的日历触发点。

- 单调时间（monotonic time）：``Clock.monotonic()``，对应 ``time.monotonic()``。
  保证单调不减，不受系统时间调整影响，但不同机器/进程间不可比较。
  适用场景：测量间隔——超时、截止时间、重试退避、限流、耗时统计。

规则：凡是"经过多久"的判定一律用单调时间；凡是"到某个日历时刻"
的判定一律用墙上时间。两者不得混用（例如不允许 wall_a - wall_b
来估算间隔）。
"""

import threading
import time


class Clock:
    """时钟接口。所有超时/过期/重试逻辑只依赖此接口。"""

    def wall(self):
        """墙上时间，Unix 秒。可能回拨。"""
        raise NotImplementedError

    def monotonic(self):
        """单调时间，秒。保证单调不减，只能用于测量间隔。"""
        raise NotImplementedError

    def sleep(self, seconds):
        """睡眠指定秒数（按单调时间计）。"""
        raise NotImplementedError


class SystemClock(Clock):
    """生产环境时钟，直接委托给标准库。"""

    def wall(self):
        return time.time()

    def monotonic(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)


class FakeClock(Clock):
    """可注入的确定性假时钟。

    - ``sleep()`` 不做真实等待，直接把时钟向前推进对应时长，
      因此超时/过期/重试逻辑可以完全确定地测试。
    - ``set_wall()`` 允许把墙上时间往回拨，用于模拟 NTP 校时回拨；
      单调时间不受任何影响，永远单调不减。
    - 所有读写都持有同一把锁，多线程并发读取安全。
    """

    def __init__(self, wall=0.0, mono=0.0):
        self._lock = threading.Lock()
        self._wall = float(wall)
        self._mono = float(mono)
        self.slept_total = 0.0  # 累计"睡眠"时长，便于断言退避序列

    def wall(self):
        with self._lock:
            return self._wall

    def monotonic(self):
        with self._lock:
            return self._mono

    def sleep(self, seconds):
        if seconds < 0:
            raise ValueError("cannot sleep for a negative duration")
        self.advance(seconds)

    def advance(self, seconds):
        """同时推进墙上时间与单调时间（模拟时间正常流逝）。"""
        if seconds < 0:
            raise ValueError("advance() only moves time forward; "
                             "use set_wall() to move the wall clock back")
        with self._lock:
            self._wall += seconds
            self._mono += seconds
            self.slept_total += seconds

    def set_wall(self, timestamp):
        """设置墙上时间（可回拨），单调时间保持不变。"""
        with self._lock:
            self._wall = float(timestamp)
