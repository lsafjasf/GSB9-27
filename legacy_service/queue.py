import os


def describe_consumer():
    broker = os.getenv("QUEUE_BROKER", "amqp://localhost")
    concurrency = int(os.getenv("QUEUE_CONCURRENCY", "4"))
    durable = os.getenv("QUEUE_DURABLE", "1").strip().lower() in ("1", "true", "yes")
    return f"queue: broker={broker!r} concurrency={concurrency} durable={durable}"
