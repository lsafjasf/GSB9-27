"""Minimal glob matcher supporting `**`, `*` and `?` (stdlib only).

`fnmatch` treats `**` like `*` and `pathlib.PurePath.match` has surprising
semantics for nested paths, so we translate patterns to regexes ourselves:

- `**/` matches zero or more path segments (including none)
- `**`  matches any string (including `/`)
- `*`   matches any string except `/`
- `?`   matches a single character except `/`
"""

import re

_cache = {}


def _translate(pattern):
    parts = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if pattern.startswith("**/", i):
                parts.append("(?:.*/)?")
                i += 3
            elif pattern.startswith("**", i):
                parts.append(".*")
                i += 2
            else:
                parts.append("[^/]*")
                i += 1
        elif ch == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(ch))
            i += 1
    return "^" + "".join(parts) + "$"


def match(path, pattern):
    """Return True if repo-root-relative `path` matches `pattern`."""
    regex = _cache.get(pattern)
    if regex is None:
        regex = re.compile(_translate(pattern))
        _cache[pattern] = regex
    return regex.match(path) is not None


def any_match(path, patterns):
    return any(match(path, p) for p in patterns)
