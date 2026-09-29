def describe_queue(cfg):
    return (
        f"queue: broker={cfg.queue.broker!r} concurrency={cfg.queue.concurrency} "
        f"durable={cfg.queue.durable}"
    )
