"""The single source of truth for configuration.

To add a configuration key, append one Spec here and access it through the
injected config object. No other file needs changes.
"""

from .config_schema import Spec

SPECS = [
    Spec("db.host", "DB_HOST", type="str", required=True),
    Spec("db.port", "DB_PORT", type="int", default=5432, minimum=1, maximum=65535),
    Spec("db.user", "DB_USER", type="str", default="app"),
    Spec("db.pool_size", "DB_POOL_SIZE", type="int", default=10, minimum=1, maximum=100),
    Spec("db.ssl", "DB_SSL", type="bool", default=True, blank="reject"),

    Spec("cache.ttl", "CACHE_TTL", type="int", default=300, minimum=0),
    Spec("cache.prefix", "CACHE_PREFIX", type="str", required=True, blank="reject"),

    Spec("queue.broker", "QUEUE_BROKER", type="str", default="amqp://localhost"),
    Spec("queue.concurrency", "QUEUE_CONCURRENCY", type="int", default=4, minimum=1, maximum=64),
    Spec("queue.durable", "QUEUE_DURABLE", type="bool", default=True, blank="reject"),

    Spec("auth.secret", "JWT_SECRET", type="str", required=True, blank="reject"),
    Spec("auth.ttl_minutes", "JWT_TTL_MINUTES", type="int", default=60, minimum=1),
    Spec("auth.issuer", "JWT_ISSUER", type="str", default=None),

    Spec("runtime.workers", "APP_WORKERS", type="int", default=2, minimum=1, maximum=32),
    Spec("runtime.port", "APP_PORT", type="int", default=8080, minimum=1, maximum=65535),

    Spec("observability.log_level", "LOG_LEVEL", type="str",
         default="info", choices={"debug", "info", "warning", "error"}),
]
