"""Content validators: strong ETag and Last-Modified.

Both validators identify one stored revision of a resource.

* The ETag is derived from the *content bytes* (SHA-256), so identical
  bytes always produce the same validator even if the metadata changed.
* Last-Modified carries the revision's modification timestamp with the
  one-second resolution used by HTTP date strings.

Only the Python standard library is used.
"""

from __future__ import annotations

import email.utils
import hashlib
import re
from datetime import datetime, timezone

# RFC 9110 entity-tag:
#   entity-tag = [ weak ] opaque-tag
#   opaque-tag = DQUOTE *etagc DQUOTE
#   etagc      = %x21 / %x23-7E / obs-text  (anything but DQUOTE / controls)
_ETAGC = r'[\x21\x23-\x7e\x80-\xff]'
_ETAG_RE = re.compile(r'^((?:W/)?)("' + _ETAGC + r'*")$')

# One If-None-Match member, captured in full.
_LIST_MEMBER_RE = re.compile(r'\s*((?:W/)?"' + _ETAGC + r'*"|\*)\s*')


def make_etag(content: bytes) -> str:
    """Return a strong opaque ETag for ``content`` bytes.

    The same bytes always map to the same ETag; a single bit flip maps to
    a different ETag with overwhelming probability.
    """
    digest = hashlib.sha256(content).hexdigest()
    return '"' + digest + '"'


def is_weak(etag: str) -> bool:
    """True for syntactically valid weak validators (``W/"..."``)."""
    match = _ETAG_RE.fullmatch(etag)
    return bool(match and match.group(1))


def parse_etag(value: str) -> str | None:
    """Return a validated single entity-tag, else ``None`` (forged/garbage)."""
    if value is None:
        return None
    match = _ETAG_RE.fullmatch(value.strip())
    return match.group(0) if match else None


def parse_if_none_match(value: str | None) -> list[str] | None:
    """Parse an If-None-Match header.

    Returns the member list (entity-tags and/or ``'*'``), or ``None`` when
    the header is absent. A malformed member makes the *whole* header
    invalid per RFC 9110, in which case an empty list is returned so the
    caller can ignore it.
    """
    if value is None:
        return None
    members: list[str] = []
    pos = 0
    while pos < len(value):
        match = _LIST_MEMBER_RE.match(value, pos)
        if not match:
            return []
        members.append(match.group(1))
        pos = match.end()
        if pos < len(value):
            if value[pos] != ",":
                return []
            pos += 1
    return members


def strong_equal(a: str, b: str) -> bool:
    """Strong comparison (RFC 9110): both tags strong and opaque strings equal."""
    ma, mb = _ETAG_RE.fullmatch(a), _ETAG_RE.fullmatch(b)
    return bool(
        ma
        and mb
        and ma.group(1) == ""
        and mb.group(1) == ""
        and ma.group(2) == mb.group(2)
    )


def weak_equal(a: str, b: str) -> bool:
    """Weak comparison (RFC 9110): ignore the weakness indicator."""
    ma, mb = _ETAG_RE.fullmatch(a), _ETAG_RE.fullmatch(b)
    return bool(ma and mb and ma.group(2) == mb.group(2))


def any_match(members: list[str], current: str) -> bool:
    """True if an If-None-Match member matches the current strong ETag.

    ``*`` matches every existing representation. Weak tags compare weakly,
    strong tags strongly.
    """
    for member in members:
        if member == "*":
            return True
        if is_weak(member):
            if weak_equal(member, current):
                return True
        elif strong_equal(member, current):
            return True
    return False


def http_date(timestamp: float) -> str:
    """Format a POSIX timestamp as an RFC 7231 IMF-fixdate (GMT)."""
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return email.utils.format_datetime(dt, usegmt=True)


def parse_http_date(value: str | None) -> float | None:
    """Parse an HTTP date to a POSIX timestamp, or ``None`` if malformed.

    Only a valid IMF-fixdate (the single format senders must produce) is
    accepted; garbage such as ``"not-a-date"`` or ``"13"`` fails closed.
    """
    if value is None:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None or dt.tzinfo is None:
        return None
    return dt.timestamp()


def truncated_to_seconds(timestamp: float) -> float:
    """HTTP dates only carry whole seconds; comparisons must match."""
    return float(int(timestamp))
