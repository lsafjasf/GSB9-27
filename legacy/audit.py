"""审计日志（遗留）：捕获 Exception 过宽，权限错误也被重试。"""
import time


def log_event(event, transport):
    attempts = 0
    delay = 0.15
    while True:
        try:
            return transport("POST", "/audit/events", event)
        except Exception:
            attempts += 1
            if attempts >= 3:
                raise
            time.sleep(min(delay, 2))
            delay *= 2
