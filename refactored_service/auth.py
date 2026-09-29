def describe_security(cfg):
    return (
        f"auth: secret_set={bool(cfg.auth.secret)} ttl={cfg.auth.ttl_minutes} "
        f"issuer={cfg.auth.issuer!r}"
    )
