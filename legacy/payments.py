"""支付服务（遗留）：内联手写重试，参数硬编码。"""
import time

from errors import RateLimitError, TransientError


def get_balance(account_id, transport):
    attempts = 0
    delay = 0.5
    while True:
        try:
            return transport("GET", "/accounts/%s/balance" % account_id)
        except TransientError:
            attempts += 1
            if attempts >= 3:
                raise
            time.sleep(delay)
            delay *= 2


def charge(order_id, amount, transport):
    attempts = 0
    delay = 1.0
    while True:
        try:
            return transport(
                "POST", "/orders/%s/charge" % order_id, {"amount": amount}
            )
        except (TransientError, RateLimitError):
            attempts += 1
            if attempts >= 4:
                raise
            time.sleep(min(delay, 10))
            delay *= 2
