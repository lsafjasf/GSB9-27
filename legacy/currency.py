"""汇率服务（遗留）：从 1 开始计数的重试循环。"""
import time

from errors import TransientError


def get_rate(pair, transport):
    attempt = 1
    delay = 0.3
    while True:
        try:
            return transport("GET", "/fx/%s" % pair)
        except TransientError:
            if attempt >= 3:
                raise
            time.sleep(min(delay, 5))
            delay *= 2
            attempt += 1
