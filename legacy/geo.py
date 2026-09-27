"""地理定位服务（遗留）：for 循环 + 固定间隔，仅重试限流。"""
import time

from errors import RateLimitError


def locate_ip(ip, transport):
    for attempt in range(3):
        try:
            return transport("GET", "/geo/%s" % ip)
        except RateLimitError:
            if attempt == 2:
                raise
            time.sleep(0.4)
