"""定价服务（遗留）：手写重试。"""
import time

from errors import TransientError


def get_quote(sku, transport):
    attempts = 0
    delay = 0.25
    while True:
        try:
            return transport("GET", "/pricing/%s/quote" % sku)
        except TransientError:
            attempts += 1
            if attempts >= 3:
                raise
            time.sleep(min(delay, 2))
            delay *= 2
