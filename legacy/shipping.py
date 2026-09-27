"""物流服务（遗留）：错误地把 404 当成可重试。"""
import time

from errors import NotFoundError, TransientError


def create_label(order_id, transport):
    attempts = 0
    delay = 0.6
    while True:
        try:
            return transport("POST", "/orders/%s/label" % order_id)
        except (TransientError, NotFoundError):
            attempts += 1
            if attempts >= 4:
                raise
            time.sleep(min(delay, 8))
            delay *= 2
