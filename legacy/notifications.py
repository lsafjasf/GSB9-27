"""通知服务（遗留）：捕获 Exception 过宽，校验错误也被重试。"""
import time


def send_email(address, subject, transport):
    attempts = 0
    delay = 0.1
    while True:
        try:
            return transport(
                "POST", "/email/send", {"to": address, "subject": subject}
            )
        except Exception:
            attempts += 1
            if attempts >= 5:
                raise
            time.sleep(min(delay, 2))
            delay *= 2
