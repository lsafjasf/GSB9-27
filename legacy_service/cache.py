import os


def describe_backend():
    ttl = int(os.getenv("CACHE_TTL", "300"))
    prefix = os.environ["CACHE_PREFIX"]
    return f"cache: ttl={ttl} prefix={prefix!r}"
