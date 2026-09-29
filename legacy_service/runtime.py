import os


def describe_runtime():
    workers = int(os.getenv("APP_WORKERS", "2"))
    port = int(os.getenv("APP_PORT", "8080"))
    return f"runtime: workers={workers} port={port}"
