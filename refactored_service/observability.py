import logging


def describe_logging(cfg):
    level = cfg.observability.log_level.upper()
    logging.getLogger().setLevel(level)
    return f"observability: level={level}"
