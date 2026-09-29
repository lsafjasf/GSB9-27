from . import auth, cache, db, observability, queue, runtime
from .config_loader import load_config
from .config_errors import ConfigError

_READERS = (
    db.describe_connection,
    cache.describe_backend,
    queue.describe_queue,
    auth.describe_security,
    runtime.describe_runtime,
    observability.describe_logging,
)


def start(environ=None):
    cfg = load_config(environ)
    return [reader(cfg) for reader in _READERS]
