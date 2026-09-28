"""Locale-aware key normalization with selectable equivalence rule sets.

Equivalence between keys is decided by mapping every key to a *canonical
form*; two keys are equivalent iff their canonical forms are equal.
Because equality of canonical forms is an equivalence relation,
reflexivity, symmetry and transitivity hold by construction (and are also
checked exhaustively by ``verify_equivalence.py``).

A rule set (:class:`RuleSet`) is a pure-data bundle of four independent
switches, each governing one folding axis:

``case``
    ``"fold"``  - full Unicode caseless matching (``casefold``),
    ``"none"``  - casing is significant.
    A locale pre-map (``"turkic"``) changes how I/i are folded.

``width``
    ``"nfkc"`` - full-width/half-width and compatibility characters are
                 unified (e.g. ``ＡＢＣ`` = ``ABC``, ``ｶﾀｶﾅ`` = ``カタカナ``,
                 ``µ`` = ``μ``),
    ``"nfc"``  - only canonical composition; compatibility forms differ.

``combining``
    ``"compose"`` - combining-mark sequences and precomposed characters
                    are unified (``é`` = ``e\\u0301``),
    ``"preserve"`` - the exact encoding sequence is significant.

``diacritics``
    ``"keep"`` - vowel/consonant marks are significant (``café`` ≠ ``cafe``),
    ``"strip"`` - combining marks (Unicode category ``Mn``/``Me``) are
                  removed (``café`` = ``cafe``).  Aggressive: raises hit
                  rate at the cost of false merges; reported separately.

Bundled rule sets: ``strict``, ``latin_caseless`` (default),
``latin_nodiacritics``, ``turkic``, ``ja_caseless``.
Language tags resolve to rule sets through :data:`LANGUAGE_RULESETS`.

All transformations are pure standard-library Unicode; rule sets are
immutable tuples, so a stored ``(language, rule set name)`` pair always
reproduces the same partition.
"""

import unicodedata

__all__ = [
    "RuleSet",
    "RULESETS",
    "LANGUAGE_RULESETS",
    "get_ruleset",
    "ruleset_for_language",
    "register_ruleset",
    "normalize_key",
    "keys_equal",
    "canonical_key",
    "equivalence_classes",
]

_CASE_CHOICES = ("fold", "none")
_WIDTH_CHOICES = ("nfkc", "nfc")
_COMBINING_CHOICES = ("compose", "preserve")
_DIACRITIC_CHOICES = ("keep", "strip")
_CASE_LOCALE_CHOICES = ("generic", "turkic", "none")


class RuleSet(tuple):
    """An immutable, hashable bundle of the four folding switches.

    Fields: ``name, case, width, combining, diacritics, case_locale``.
    """

    __slots__ = ()

    def __new__(cls, name, case="fold", width="nfkc",
                combining="compose", diacritics="keep",
                case_locale="generic"):
        for field, value, choices in (
            ("case", case, _CASE_CHOICES),
            ("width", width, _WIDTH_CHOICES),
            ("combining", combining, _COMBINING_CHOICES),
            ("diacritics", diacritics, _DIACRITIC_CHOICES),
            ("case_locale", case_locale, _CASE_LOCALE_CHOICES),
        ):
            if value not in choices:
                raise ValueError(
                    "ruleset %r: invalid %s=%r, expected one of %r"
                    % (name, field, value, choices))
        if case == "none" and case_locale != "none":
            # The Turkic pre-map is only meaningful while folding; reject
            # incoherent combinations rather than silently ignoring them.
            raise ValueError(
                "ruleset %r: case_locale must be 'none' when case='none'"
                % name)
        return tuple.__new__(cls, (name, case, width, combining,
                                  diacritics, case_locale))

    name = property(lambda self: self[0])
    case = property(lambda self: self[1])
    width = property(lambda self: self[2])
    combining = property(lambda self: self[3])
    diacritics = property(lambda self: self[4])
    case_locale = property(lambda self: self[5])

    def __repr__(self):
        return ("RuleSet(name=%r, case=%r, width=%r, combining=%r, "
                "diacritics=%r, case_locale=%r)" % self)


# Bundled rule sets, ordered from most strict to most aggressive.
RULESETS = {
    # Identifiers / codes: nothing folds. Every surface distinction kept.
    "strict": RuleSet("strict", case="none", width="nfc",
                      combining="preserve", diacritics="keep",
                      case_locale="none"),
    # Default: locale-independent Unicode caseless matching with
    # compatibility folding and canonical composition.  Backward
    # compatible with the previous "default" profile.
    "latin_caseless": RuleSet("latin_caseless"),
    # Same as default, but diacritics are removed too.  High hit rate,
    # high false-merge risk (café/cafe, señor/senor collapse).
    "latin_nodiacritics": RuleSet("latin_nodiacritics", diacritics="strip"),
    # Turkic (tr/az/kk) casing: "I" -> dotless ı, "İ" -> i.
    "turkic": RuleSet("turkic", case_locale="turkic"),
    # Japanese content: full/half-width katakana and ASCII width are
    # unified; latin text is still case-folded.
    "ja_caseless": RuleSet("ja_caseless"),
}

# Language tag -> rule set name.  ``*_ascii`` variants opt in to the
# strict identifier rules for code/ID-like fields in that language.
LANGUAGE_RULESETS = {
    "en": "latin_caseless",
    "de": "latin_caseless",
    "fr": "latin_caseless",
    "es": "latin_caseless",
    "sv": "latin_caseless",
    "el": "latin_caseless",
    "tr": "turkic",
    "az": "turkic",
    "kk": "turkic",
    "ja": "ja_caseless",
    "en_ascii": "strict",
    "tr_ascii": "strict",
}

# Backward-compatible aliases from the previous two-profile API.
_PROFILE_ALIASES = {"default": "latin_caseless", "turkic": "turkic"}

# Turkic (tr/az) casing: dotted I and dotless i are distinct letters.
# Applied before casefold so Python's locale-independent casefold does
# not map "I" -> "i".
_TURKIC_PRE_MAP = str.maketrans({
    "I": "ı",   # U+0049 LATIN CAPITAL I      -> U+0131 dotless i
    "İ": "i",   # U+0130 CAPITAL I WITH DOT   -> U+0069 small i
})


def get_ruleset(name):
    """Return the registered :class:`RuleSet` for *name*.

    Accepts the legacy aliases ``"default"`` and a ``RuleSet`` instance.
    """
    if isinstance(name, RuleSet):
        return name
    resolved = _PROFILE_ALIASES.get(name, name)
    try:
        return RULESETS[resolved]
    except KeyError:
        raise ValueError(
            "unknown rule set %r, expected one of %r"
            % (name, sorted(RULESETS))) from None


def ruleset_for_language(language_tag):
    """Resolve a language tag (e.g. ``"tr"``) to its :class:`RuleSet`."""
    try:
        return get_ruleset(LANGUAGE_RULESETS[language_tag])
    except KeyError:
        raise ValueError(
            "no rule set configured for language tag %r, known tags: %r"
            % (language_tag, sorted(LANGUAGE_RULESETS))) from None


def register_ruleset(ruleset, language_tags=()):
    """Register a custom :class:`RuleSet` (and optional language tags).

    Rule resolution is data-driven: registering is enough for
    :func:`get_ruleset` / :func:`ruleset_for_language` to pick it up.
    """
    if not isinstance(ruleset, RuleSet):
        raise TypeError("expected a RuleSet, got %r" % type(ruleset).__name__)
    RULESETS[ruleset.name] = ruleset
    for tag in language_tags:
        LANGUAGE_RULESETS[tag] = ruleset.name
    return ruleset


def _strip_marks(text):
    """Remove non-spacing/enclosing combining marks (Mn/Me)."""
    return "".join(ch for ch in text
                   if unicodedata.category(ch) not in ("Mn", "Me"))


def normalize_key(key, ruleset="latin_caseless"):
    """Return the canonical form of *key* under *ruleset*.

    *ruleset* is a rule-set name, a legacy profile name (``"default"``)
    or a :class:`RuleSet` instance.  The result is idempotent:
    ``normalize_key(normalize_key(k), rs) == normalize_key(k, rs)`` for
    every rule set and key.
    """
    if not isinstance(key, str):
        raise TypeError("key must be a str, got %r" % type(key).__name__)
    rs = get_ruleset(ruleset)

    # 1. Compatibility / width folding (also composes canonically).
    text = unicodedata.normalize("NFKC" if rs.width == "nfkc" else "NFC", key)

    # 2. Locale-specific casing pre-map, then full case folding.
    if rs.case == "fold":
        if rs.case_locale == "turkic":
            text = text.translate(_TURKIC_PRE_MAP)
        text = text.casefold()

    # 3. Re-compose / fold width: folding can introduce new sequences
    #    (e.g. casefold('İ') -> 'i\u0307') or new compatibility chars.
    text = unicodedata.normalize("NFKC" if rs.width == "nfkc" else "NFC", text)

    # 4. Combining-mark handling.  Decompose first so marks embedded in
    #    precomposed letters (Â -> A + ^) are removed too, then recompose
    #    whatever marks remain (none, since strip removes all of them).
    if rs.diacritics == "strip":
        text = unicodedata.normalize("NFC",
                                     _strip_marks(unicodedata.normalize("NFD", text)))

    # 5. Final composition pass so the output is a fixed point.
    return unicodedata.normalize(
        "NFKC" if (rs.width == "nfkc" or rs.diacritics == "strip"
                   or rs.combining == "compose") else "NFC", text)


def keys_equal(a, b, ruleset="latin_caseless"):
    """Return True iff *a* and *b* are equivalent under *ruleset*."""
    return normalize_key(a, ruleset) == normalize_key(b, ruleset)


def canonical_key(key, ruleset="latin_caseless"):
    """Canonical form suitable as a dict key for lookup/dedup indexes."""
    return normalize_key(key, ruleset)


def equivalence_classes(keys, ruleset="latin_caseless"):
    """Partition *keys* into equivalence classes under *ruleset*.

    Returns a list of lists (sorted by canonical form); each inner list
    contains the input keys sharing one canonical form, in first-seen
    order.  The result is a true partition: every key appears exactly
    once and classes are disjoint.
    """
    rs = get_ruleset(ruleset)
    buckets = {}
    order = []
    for key in keys:
        canon = normalize_key(key, rs)
        if canon not in buckets:
            buckets[canon] = []
            order.append(canon)
        buckets[canon].append(key)
    return [buckets[canon] for canon in sorted(order, key=lambda c: (c,))]
