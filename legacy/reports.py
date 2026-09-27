"""报表服务（遗留）：抖动 + 超时预算。"""
import random
import time

from errors import TransientError


def export_report(report_id, transport):
    attempts = 0
    delay = 1.5
    start = time.monotonic()
    while True:
        try:
            return transport("GET", "/reports/%s/export" % report_id)
        except TransientError:
            attempts += 1
            if attempts >= 5 or time.monotonic() - start > 90:
                raise
            wait = min(delay, 20)
            time.sleep(wait * random.uniform(0.5, 1.5))
            delay *= 2
