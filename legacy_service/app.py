from . import auth, cache, db, observability, queue, runtime


def start():
    return [
        db.describe_connection(),
        cache.describe_backend(),
        queue.describe_consumer(),
        auth.describe_security(),
        runtime.describe_runtime(),
        observability.describe_logging(),
    ]
