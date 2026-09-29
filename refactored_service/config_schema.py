import os

from .config_errors import ConfigError

_MISSING = object()


class Spec:
    """Declarative definition of one configuration key.

    path        dotted key path, e.g. "db.host"
    env         environment variable that supplies the raw value
    type        "str" | "int" | "bool"
    required    the key must resolve to a usable value
    default     used when the variable is unset (or blank when blank == "default")
    choices     allowed value set (checked after coercion)
    minimum     inclusive numeric lower bound
    maximum     inclusive numeric upper bound
    blank       "default" -> blank input falls back to the default/required rule;
                "reject"  -> blank input is always a type error
    """

    __slots__ = (
        "path", "env", "type", "required", "default",
        "choices", "minimum", "maximum", "blank",
    )

    def __init__(self, path, env, type="str", required=False, default=_MISSING,
                 choices=None, minimum=None, maximum=None, blank="default"):
        self.path = path
        self.env = env
        self.type = type
        self.required = required
        self.default = default
        self.choices = choices
        self.minimum = minimum
        self.maximum = maximum
        self.blank = blank


class _Node:
    """Attribute/dict view over the validated flat key map."""

    __slots__ = ("_values", "_prefix")

    def __init__(self, values, prefix=""):
        object.__setattr__(self, "_values", values)
        object.__setattr__(self, "_prefix", prefix)

    def get(self, path, default=_MISSING):
        full = f"{self._prefix}.{path}" if self._prefix else path
        if full in self._values:
            return self._values[full]
        if default is not _MISSING:
            return default
        raise KeyError(full)

    def __getitem__(self, path):
        return self.get(path)

    def __contains__(self, path):
        full = f"{self._prefix}.{path}" if self._prefix else path
        return full in self._values

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        prefix = f"{self._prefix}.{name}" if self._prefix else name
        own = f"{self._prefix}.{name}" if self._prefix else name
        if own in self._values:
            return self._values[own]
        if any(key == prefix or key.startswith(prefix + ".") for key in self._values):
            return _Node(self._values, prefix)
        raise AttributeError(name)


def _coerce_bool(spec, raw):
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise ConfigError(
        f"{spec.path} (env {spec.env}): expected bool, got {raw!r}; "
        "allowed forms: 1/0, true/false, yes/no, on/off"
    )


def _coerce_int(spec, raw):
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        raise ConfigError(
            f"{spec.path} (env {spec.env}): expected int, got {raw!r}"
        ) from None


def _coerce(spec, raw):
    if spec.type == "str":
        return raw
    if spec.type == "int":
        return _coerce_int(spec, raw)
    if spec.type == "bool":
        return _coerce_bool(spec, raw)
    raise ConfigError(f"{spec.path}: unknown type {spec.type!r}")


def _is_blank(raw):
    return isinstance(raw, str) and raw.strip() == ""


def _resolve_spec(spec, environ, errors):
    raw = environ.get(spec.env, _MISSING)

    if raw is _MISSING or (_is_blank(raw) and spec.blank == "default"):
        if spec.required:
            errors.append(
                f"{spec.path} (env {spec.env}): required value is missing"
                + (" or blank" if raw is not _MISSING else "")
            )
            return _MISSING
        if spec.default is not _MISSING:
            return spec.default
        return _MISSING

    if _is_blank(raw):
        errors.append(
            f"{spec.path} (env {spec.env}): blank value is not allowed for "
            f"a {spec.type} key"
        )
        return _MISSING

    try:
        value = _coerce(spec, raw)
    except ConfigError as exc:
        errors.append(str(exc))
        return _MISSING

    if spec.choices is not None and value not in spec.choices:
        errors.append(
            f"{spec.path} (env {spec.env}): value {value!r} is outside "
            f"allowed choices {sorted(spec.choices)!r}"
        )
    if spec.minimum is not None and value < spec.minimum:
        errors.append(
            f"{spec.path} (env {spec.env}): value {value!r} is below "
            f"minimum {spec.minimum}"
        )
    if spec.maximum is not None and value > spec.maximum:
        errors.append(
            f"{spec.path} (env {spec.env}): value {value!r} is above "
            f"maximum {spec.maximum}"
        )
    return value


def build_config(specs, environ=None):
    """Validate every spec once and return an immutable nested config view.

    All problems are collected and raised together so a single startup
    reports every offending key path.
    """
    if environ is None:
        environ = os.environ
    errors = []
    values = {}
    for spec in specs:
        value = _resolve_spec(spec, environ, errors)
        if value is not _MISSING:
            values[spec.path] = value
    if errors:
        raise ConfigError(
            "configuration validation failed:\n  - " + "\n  - ".join(errors)
        )
    return _Node(values)
