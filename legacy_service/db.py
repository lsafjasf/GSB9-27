import os


def describe_connection():
    host = os.getenv("DB_HOST")
    port = int(os.environ.get("DB_PORT", "5432"))
    user = os.getenv("DB_USER", "app")
    pool_size = int(os.getenv("DB_POOL_SIZE", "10"))
    ssl = os.getenv("DB_SSL", "true").strip().lower() in ("1", "true", "yes")
    return (
        f"db: host={host!r} port={port} user={user!r} "
        f"pool_size={pool_size} ssl={ssl}"
    )
