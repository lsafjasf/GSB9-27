"""搜索服务（遗留）：短重试。"""
import time

from errors import TransientError


def query(text, transport):
    attempts = 0
    delay = 0.4
    while True:
        try:
            return transport("GET", "/search", {"q": text})
        except TransientError:
            attempts += 1
            if attempts >= 2:
                raise
            time.sleep(min(delay, 1.5))
            delay *= 2
