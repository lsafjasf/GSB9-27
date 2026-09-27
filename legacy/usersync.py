"""用户同步（遗留）：带总超时预算的手写重试。"""
import time

from errors import RateLimitError, TransientError


def sync_user(user_id, transport):
    attempts = 0
    delay = 1.0
    start = time.monotonic()
    while True:
        try:
            return transport("POST", "/users/%s/sync" % user_id)
        except (TransientError, RateLimitError):
            attempts += 1
            if attempts >= 8 or time.monotonic() - start > 20:
                raise
            time.sleep(min(delay, 8))
            delay *= 2
