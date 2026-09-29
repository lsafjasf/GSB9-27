def describe_connection(cfg):
    return (
        f"db: host={cfg.db.host!r} port={cfg.db.port} user={cfg.db.user!r} "
        f"pool_size={cfg.db.pool_size} ssl={cfg.db.ssl}"
    )
