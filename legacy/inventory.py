"""库存服务（遗留）：带抖动的手写重试。"""
import random
import time

from errors import TransientError


def reserve(sku, quantity, transport):
    attempts = 0
    delay = 0.2
    while True:
        try:
            return transport(
                "POST", "/inventory/%s/reserve" % sku, {"quantity": quantity}
            )
        except TransientError:
            attempts += 1
            if attempts >= 3:
                raise
            wait = min(delay, 5)
            time.sleep(wait * random.uniform(0.5, 1.5))
            delay *= 3
