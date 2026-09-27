"""账单服务（遗留）：捕获 Exception 过宽。"""
import time


def create_invoice(order_id, transport):
    attempts = 0
    delay = 0.6
    while True:
        try:
            return transport("POST", "/orders/%s/invoice" % order_id)
        except Exception:
            attempts += 1
            if attempts >= 3:
                raise
            time.sleep(min(delay, 5))
            delay *= 2
