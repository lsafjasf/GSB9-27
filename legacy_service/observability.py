import os
import logging


def describe_logging():
    level = os.getenv("LOG_LEVEL", "info").upper()
    logging.getLogger().setLevel(level)
    return f"observability: level={level}"
