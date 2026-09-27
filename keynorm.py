"""Locale-aware key normalization for dictionary lookup and deduplication.

Replaces naive ``str.lower()`` comparison, which is broken for:

- case variants outside ASCII (``"SS"`` vs ``"ß"``),
- full-width vs half-width forms (``"ＡＢＣ"`` vs ``"abc"``),
- canonically equivalent combining sequences (``"é"`` vs ``"e\\u0301"``),
- locale-specific casing (Turkish dotted/dotless i).

The fix maps every key to a canonical form; two keys are equivalent iff
their canonical forms are equal. Equality of canonical forms is an
equivalence relation, so reflexivity, symmetry and transitivity hold by
construction.
"""

import unicodedata

PROFILES = ("default", "turkic")

# Turkic (tr/az) casing: dotted I and dotless i are distinct letters.
# Apply before casefold so Python's locale-independent casefold does not
# map "I" to "i".
_TURKIC_PRE_MAP = str.maketrans({
    "I": "ı",   # U+0049 LATIN CAPITAL I      -> U+0131 dotless i
    "İ": "i",   # U+0130 CAPITAL I WITH DOT   -> U+0069 small i
})


def normalize_key(key, profile="default"):
    """Return the canonical form of *key* under the given locale profile.

    Profiles:
      - ``"default"``: locale-independent Unicode default caseless
        matching. Full-width/half-width and other compatibility variants
        are unified (NFKC), then case-folded. ``"I"`` and ``"i"`` are
        equivalent; ``"İ"`` and ``"i"`` are not.
      - ``"turkic"``: same pipeline, but with Turkic casing for I/i:
        ``"I"`` folds to dotless ``"ı"`` and ``"İ"`` folds to ``"i"``.
        Consequently ``"I"`` and ``"i"`` are NOT equivalent here, while
        ``"İ"`` and ``"i"`` are.

    The result is idempotent: ``normalize_key(normalize_key(k)) ==
    normalize_key(k)`` for any key, so canonical forms can be stored and
    re-normalized safely.
    """
    if not isinstance(key, str):
        raise TypeError("key must be a str, got %r" % type(key).__name__)
    if profile not in PROFILES:
        raise ValueError("unknown profile %r, expected one of %r"
                         % (profile, PROFILES))
    if profile == "turkic":
        key = key.translate(_TURKIC_PRE_MAP)
    # NFKC unifies full/half width and compatibility characters and
    # composes combining sequences; casefold performs full caseless
    # matching (e.g. "ß" -> "ss"). A second NFKC pass keeps the result
    # idempotent because casefold can introduce new sequences.
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", key).casefold())


def keys_equal(a, b, profile="default"):
    """Return True iff *a* and *b* are equivalent under *profile*."""
    return normalize_key(a, profile) == normalize_key(b, profile)


def canonical_key(key, profile="default"):
    """Canonical form suitable as a dict key for lookup/dedup indexes."""
    return normalize_key(key, profile)
