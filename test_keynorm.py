"""Regression and equivalence-class tests for keynorm.

Run:  python3 -m unittest test_keynorm -v
"""

import itertools
import unittest

from keynorm import canonical_key, keys_equal, normalize_key


# Equivalence classes under the "default" profile: every key inside one
# group must be equivalent to every other key in the same group, and to
# no key in any other group.
DEFAULT_EQUIVALENCE_CLASSES = [
    # case variants, incl. full case folding (sharp s -> ss)
    ["STRASSE", "strasse", "Straße", "straße"],
    # Greek final/medial sigma
    ["οδος", "οδοσ", "ΟΔΟΣ"],
    # full-width vs half-width, letters and digits
    ["ＡＢＣ１２３", "abc123", "Ａbc１２3"],
    # half-width vs full-width katakana
    ["ｶﾀｶﾅ", "カタカナ"],
    # combining sequences vs precomposed, with diacritics
    ["café", "café", "CAFÉ", "Café"],
    ["Ångström", "ångström", "Ångström", "Ångström"],
    ["señor", "señor", "SEÑOR"],
    # micro sign / Greek mu compatibility
    ["µm", "μm", "ΜM"],
]

# Keys from different classes above must NOT be equivalent; also add
# explicit negative pairs where dropping a diacritic would change meaning.
DEFAULT_NEGATIVE_PAIRS = [
    ("café", "cafe"),        # diacritics are significant
    ("señor", "senor"),
    ("straße", "strasz"),    # near-miss
    ("abc123", "abc124"),
]

# Differences between the default and turkic profiles.
TURKIC_EQUAL_PAIRS = [
    ("İSTANBUL", "istanbul"),
    ("İ", "i"),
    ("I", "ı"),
    ("IĞDIR", "ığdır"),
]

TURKIC_UNEQUAL_PAIRS = [
    ("I", "i"),              # distinct letters in Turkic casing
    ("İ", "ı"),
    ("IĞDIR", "iğdir"),
]


def all_keys(classes):
    return [key for group in classes for key in group]


class TestEquivalenceRelation(unittest.TestCase):
    """The equivalence induced by normalize_key must be self-consistent:
    reflexive, symmetric and transitive."""

    def assert_equivalence_class_laws(self, keys, profile):
        for key in keys:
            # reflexive: every key is equivalent to itself
            self.assertTrue(keys_equal(key, key, profile),
                            "reflexivity failed for %r" % key)
        for a, b in itertools.combinations(keys, 2):
            # symmetric
            self.assertEqual(keys_equal(a, b, profile),
                             keys_equal(b, a, profile),
                             "symmetry failed for %r, %r" % (a, b))
        for a, b, c in itertools.permutations(keys, 3):
            # transitive: a~b and b~c implies a~c
            if keys_equal(a, b, profile) and keys_equal(b, c, profile):
                self.assertTrue(keys_equal(a, c, profile),
                                "transitivity failed for %r, %r, %r"
                                % (a, b, c))

    def test_default_profile_laws(self):
        self.assert_equivalence_class_laws(
            all_keys(DEFAULT_EQUIVALENCE_CLASSES), "default")

    def test_turkic_profile_laws(self):
        keys = all_keys(DEFAULT_EQUIVALENCE_CLASSES) + [
            k for pair in TURKIC_EQUAL_PAIRS + TURKIC_UNEQUAL_PAIRS
            for k in pair
        ]
        self.assert_equivalence_class_laws(keys, "turkic")

    def test_normalization_is_idempotent(self):
        for profile in ("default", "turkic"):
            for key in all_keys(DEFAULT_EQUIVALENCE_CLASSES):
                once = normalize_key(key, profile)
                self.assertEqual(once, normalize_key(once, profile))


class TestEquivalenceClasses(unittest.TestCase):
    def test_members_of_each_class_are_pairwise_equivalent(self):
        for group in DEFAULT_EQUIVALENCE_CLASSES:
            for a, b in itertools.combinations(group, 2):
                self.assertTrue(
                    keys_equal(a, b),
                    "%r and %r should be equivalent" % (a, b))

    def test_distinct_classes_are_not_equivalent(self):
        for g1, g2 in itertools.combinations(DEFAULT_EQUIVALENCE_CLASSES, 2):
            for a, b in itertools.product(g1, g2):
                self.assertFalse(
                    keys_equal(a, b),
                    "%r and %r should NOT be equivalent" % (a, b))

    def test_explicit_negative_pairs(self):
        for a, b in DEFAULT_NEGATIVE_PAIRS:
            self.assertFalse(keys_equal(a, b),
                             "%r and %r should NOT be equivalent" % (a, b))

    def test_canonical_form_is_shared_within_class(self):
        for group in DEFAULT_EQUIVALENCE_CLASSES:
            forms = {canonical_key(k) for k in group}
            self.assertEqual(len(forms), 1,
                             "class %r produced %d canonical forms"
                             % (group, len(forms)))


class TestLocaleProfiles(unittest.TestCase):
    def test_default_profile_i_casing(self):
        # Locale-independent default: I and i match, dotted capital I
        # does not fold to plain i.
        self.assertTrue(keys_equal("I", "i"))
        self.assertFalse(keys_equal("İ", "i"))

    def test_turkic_profile_equal_pairs(self):
        for a, b in TURKIC_EQUAL_PAIRS:
            self.assertTrue(keys_equal(a, b, "turkic"),
                            "%r and %r should be equivalent (turkic)"
                            % (a, b))

    def test_turkic_profile_unequal_pairs(self):
        for a, b in TURKIC_UNEQUAL_PAIRS:
            self.assertFalse(keys_equal(a, b, "turkic"),
                             "%r and %r should NOT be equivalent (turkic)"
                             % (a, b))

    def test_profiles_differ_on_dotted_i(self):
        # The same pair must flip between profiles -- this is the
        # documented, tested difference between the two rule sets.
        self.assertFalse(keys_equal("İ", "i", "default"))
        self.assertTrue(keys_equal("İ", "i", "turkic"))
        self.assertTrue(keys_equal("I", "i", "default"))
        self.assertFalse(keys_equal("I", "i", "turkic"))

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):
            normalize_key("x", "no-such-profile")

    def test_non_string_rejected(self):
        with self.assertRaises(TypeError):
            normalize_key(123)


class TestIndexUsage(unittest.TestCase):
    """Canonical keys must work for dict lookup and dedup."""

    def test_lookup_with_equivalent_key(self):
        index = {canonical_key("ＡＢＣ１２３"): "value"}
        self.assertEqual(index.get(canonical_key("abc123")), "value")

    def test_dedup_collapses_equivalent_keys(self):
        keys = ["café", "café", "CAFÉ", "señor", "señor"]
        self.assertEqual(len({canonical_key(k) for k in keys}), 2)


if __name__ == "__main__":
    unittest.main()
