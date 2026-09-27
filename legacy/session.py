"""会话服务（遗留）：短重试。"""
import time

from errors import TransientError


def refresh_token(session_id, transport):
    attempts = 0
    delay = 0.2
    while True:
        try:
            return transport("POST", "/sessions/%s/refresh" % session_id)
        except TransientError:
            attempts += 1
            if attempts >= 2:
                raise
            time.sleep(min(delay, 0.8))
            delay *= 2
