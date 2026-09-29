from .config_definitions import SPECS
from .config_errors import ConfigError
from .config_schema import build_config


def load_config(environ=None):
    """Startup entry point: validate everything once, fail fast or proceed."""
    return build_config(SPECS, environ)
