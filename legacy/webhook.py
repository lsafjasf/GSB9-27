"""Webhook 投递（遗留）：抖动 + 限流重试。"""
import random
import time

from errors import RateLimitError, TransientError


def deliver(hook_id, payload, transport):
    attempts = 0
    delay = 0.5
    while True:
        try:
            return transport("POST", "/hooks/%s/deliver" % hook_id, payload)
        except (TransientError, RateLimitError):
            attempts += 1
            if attempts >= 5:
                raise
            wait = min(delay, 16)
            time.sleep(wait * random.uniform(0.5, 1.5))
            delay *= 2
