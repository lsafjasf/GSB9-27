def describe_backend(cfg):
    return f"cache: ttl={cfg.cache.ttl} prefix={cfg.cache.prefix!r}"
