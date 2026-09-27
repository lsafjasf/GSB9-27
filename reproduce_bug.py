"""Reproduce the key-comparison bug: naive ``str.lower()`` treats
visually/semantically identical keys as different.

Run:  python3 reproduce_bug.py

Each case prints whether the OLD implementation (``lower()``) and the
FIXED implementation (``keynorm.keys_equal``) consider the pair equal.
The old implementation fails every case; the fixed one passes all.
Exits non-zero if the old implementation is (still) correct or the new
one regresses.
"""

import sys

from keynorm import keys_equal


def old_keys_equal(a, b):
    """The buggy implementation: plain lowercase comparison."""
    return a.lower() == b.lower()


# (description, key_a, key_b, locale profile)
CASES = [
    ("case variant: German sharp s", "STRASSE", "Straße", "default"),
    ("case variant: Greek final sigma", "οδος", "οδοσ", "default"),
    ("full-width vs half-width", "ＡＢＣ１２３", "abc123", "default"),
    ("full-width vs half-width kana", "ｶﾀｶﾅ", "カタカナ", "default"),
    ("combining char: precomposed vs decomposed", "café", "café", "default"),
    ("combining char: angstrom", "Ångström", "Ångström", "default"),
    ("diacritic: precomposed vs combining tilde", "señor", "señor", "default"),
    ("turkic locale: dotted capital I", "İSTANBUL", "istanbul", "turkic"),
]


def main():
    failures = 0
    for desc, a, b, profile in CASES:
        old = old_keys_equal(a, b)
        new = keys_equal(a, b, profile)
        print("[%s] %-45s old(lower)=%-5s fixed=%s"
              % ("OK" if new else "FAIL", desc, old, new))
        if old:
            print("  note: old implementation unexpectedly passed this case")
        if not new:
            failures += 1
    if failures:
        print("\n%d case(s) still failing after the fix" % failures)
        return 1
    print("\nAll %d cases: old implementation failed, fixed implementation passes."
          % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
