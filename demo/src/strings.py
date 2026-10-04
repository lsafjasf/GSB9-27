"""String helpers used by the demo checks."""


def shout(text):
    return text.upper() + "!"


def slugify(text):
    return "-".join(text.lower().split())
