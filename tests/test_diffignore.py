"""Self-tests for diffignore, including differential (对拍) verification.

Differential property: for the same input, the diff set computed with rules
and the diff set computed without rules must differ by EXACTLY the set of
diffs the rules' audit reports as masked:
    raw == kept ∪ masked   (disjoint union)
    raw \ kept == masked
The masked set is cross-checked against an INDEPENDENT naive matcher
implemented below (naive_rule_matches), not the library's own matcher.
"""
import random
import re
import unittest

from diffignore import (
    KEY_ADDED,
    KEY_REMOVED,
    ITEM_ADDED,
    ITEM_REMOVED,
    STRUCTURAL_KINDS,
    TYPE_CHANGED,
    VALUE_CHANGED,
    Rule,
    apply_rules,
    diff,
    split_path,
)

# ---------------------------------------------------------- naive matcher (independent reference)

_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$"
)


def naive_glob(pattern, path):
    """Independent glob matcher: recursive, segment by segment."""
    segs = path.split("/")[1:] if path else []
    pats = pattern.split("/")[1:] if pattern.startswith("/") else ["**"] + pattern.split("/")

    def rec(pi, si):
        if pi == len(pats):
            return si == len(segs)
        if pats[pi] == "**":
            return any(rec(pi + 1, k) for k in range(si, len(segs) + 1))
        if si == len(segs):
            return False
        if pats[pi] == "*" or pats[pi] == segs[si]:
            return rec(pi + 1, si + 1)
        return False

    return rec(0, 0)


def naive_type_matches(type_name, old, new):
    def pred(v):
        if type_name == "iso8601":
            return isinstance(v, str) and bool(_ISO_RE.match(v))
        if type_name == "number":
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        if type_name == "integer":
            return isinstance(v, int) and not isinstance(v, bool)
        if type_name == "string":
            return isinstance(v, str)
        if type_name == "boolean":
            return isinstance(v, bool)
        if type_name == "null":
            return v is None
        raise AssertionError(type_name)

    return pred(old) and pred(new)


def naive_rule_matches(rule, d):
    if d.kind != VALUE_CHANGED:
        return False
    if rule.path is not None:
        return naive_glob(rule.path, d.path)
    return naive_type_matches(rule.type, d.old, d.new)


def keys(diffs):
    return {d.key for d in diffs}


# ---------------------------------------------------------- unit tests

class DiffEngineTests(unittest.TestCase):
    def test_value_change(self):
        d = diff({"a": 1}, {"a": 2})
        self.assertEqual([x.key for x in d], [(VALUE_CHANGED, "/a")])

    def test_structural_kinds(self):
        d = diff({"a": 1, "b": [1, 2]}, {"a": 1, "b": [1], "c": 3})
        got = keys(d)
        self.assertIn((ITEM_REMOVED, "/b/1"), got)
        self.assertIn((KEY_ADDED, "/c"), got)

    def test_array_length_change(self):
        d = diff([1, 2, 3], [1])
        self.assertEqual(keys(d), {(ITEM_REMOVED, "/1"), (ITEM_REMOVED, "/2")})

    def test_type_change(self):
        d = diff({"a": {"x": 1}}, {"a": [1]})
        self.assertEqual(keys(d), {(TYPE_CHANGED, "/a")})

    def test_bool_is_not_number(self):
        d = diff({"a": 1}, {"a": True})
        self.assertEqual(keys(d), {(TYPE_CHANGED, "/a")})


class RuleMatchTests(unittest.TestCase):
    def mk(self, **kw):
        return Rule(**kw)

    def test_exact_path(self):
        r = self.mk(id="r", path="/meta/updated_at")
        d = diff({"meta": {"updated_at": "t1"}}, {"meta": {"updated_at": "t2"}})
        res = apply_rules(d, [r])
        self.assertEqual(res.kept, [])
        self.assertEqual(len(res.masked), 1)

    def test_glob_single_segment(self):
        r = self.mk(id="r", path="/servers/*/port")
        d = diff(
            {"servers": [{"port": 1}, {"port": 2}]},
            {"servers": [{"port": 9}, {"port": 2}]},
        )
        res = apply_rules(d, [r])
        self.assertEqual(keys(res.masked), {(VALUE_CHANGED, "/servers/0/port")})
        self.assertEqual(res.kept, [])

    def test_glob_double_star_and_unanchored(self):
        r = self.mk(id="r", path="comment")  # unanchored: any depth
        d = diff(
            {"a": {"comment": "x", "b": [{"comment": "y"}]}},
            {"a": {"comment": "X", "b": [{"comment": "Y"}]}},
        )
        res = apply_rules(d, [r])
        self.assertEqual(res.kept, [])
        self.assertEqual(len(res.masked), 2)

    def test_type_iso8601(self):
        r = self.mk(id="r", type="iso8601")
        d = diff(
            {"t": "2026-09-28T10:00:00Z", "n": "hello"},
            {"t": "2026-09-28T11:00:00Z", "n": "world"},
        )
        res = apply_rules(d, [r])
        self.assertEqual(keys(res.masked), {(VALUE_CHANGED, "/t")})
        self.assertEqual(keys(res.kept), {(VALUE_CHANGED, "/n")})

    def test_type_rule_requires_both_sides(self):
        r = self.mk(id="r", type="iso8601")
        d = diff({"t": "not-a-date"}, {"t": "2026-09-28T11:00:00Z"})
        res = apply_rules(d, [r])
        self.assertEqual(len(res.kept), 1)

    def test_structural_never_masked_key_removed(self):
        # Even a wildcard rule covering the whole subtree must NOT hide a
        # deleted key.
        r = self.mk(id="r", path="/**")
        d = diff({"meta": {"ts": "a", "gone": 1}}, {"meta": {"ts": "b"}})
        res = apply_rules(d, [r])
        self.assertEqual(keys(res.kept), {(KEY_REMOVED, "/meta/gone")})
        self.assertEqual(keys(res.masked), {(VALUE_CHANGED, "/meta/ts")})

    def test_structural_never_masked_array_length(self):
        r = self.mk(id="r", path="/items/*")
        d = diff({"items": [1, 2, 3]}, {"items": [9]})
        res = apply_rules(d, [r])
        self.assertEqual(
            keys(res.kept), {(ITEM_REMOVED, "/items/1"), (ITEM_REMOVED, "/items/2")}
        )
        self.assertEqual(keys(res.masked), {(VALUE_CHANGED, "/items/0")})

    def test_structural_never_masked_type_change(self):
        r = self.mk(id="r", path="/**")
        d = diff({"a": {"x": 1}}, {"a": "flat"})
        res = apply_rules(d, [r])
        self.assertEqual(keys(res.kept), {(TYPE_CHANGED, "/a")})
        self.assertEqual(res.masked, [])


class AuditTests(unittest.TestCase):
    def test_audit_lists_masked_diffs_per_rule(self):
        rules = [
            Rule(id="ts", path="/meta/updated_at"),
            Rule(id="ports", path="/servers/*/port"),
        ]
        d = diff(
            {"meta": {"updated_at": "t1"}, "servers": [{"port": 1}, {"port": 2}]},
            {"meta": {"updated_at": "t2"}, "servers": [{"port": 8}, {"port": 9}]},
        )
        res = apply_rules(d, rules)
        by_id = {a.rule.id: a for a in res.audits}
        self.assertEqual([x.path for x in by_id["ts"].masked], ["/meta/updated_at"])
        self.assertEqual(
            {x.path for x in by_id["ports"].masked},
            {"/servers/0/port", "/servers/1/port"},
        )

    def test_dead_rule_warning(self):
        res = apply_rules(diff({"a": 1}, {"a": 2}), [Rule(id="dead", path="/nope")])
        self.assertTrue(any("未命中" in w for w in res.warnings))

    def test_over_masking_warning(self):
        old = {f"k{i}": i for i in range(20)}
        new = {f"k{i}": i + 100 for i in range(20)}
        res = apply_rules(diff(old, new), [Rule(id="all", path="/**")])
        self.assertTrue(any("屏蔽过多" in w for w in res.warnings))
        # tighten abs threshold
        res2 = apply_rules(diff(old, new), [Rule(id="all", path="/**")], warn_abs=5)
        self.assertTrue(any("绝对阈值" in w for w in res2.warnings))

    def test_first_matching_rule_gets_credit(self):
        rules = [Rule(id="r1", path="/**"), Rule(id="r2", path="/a")]
        res = apply_rules(diff({"a": 1}, {"a": 2}), rules)
        by_id = {a.rule.id: a for a in res.audits}
        self.assertEqual(len(by_id["r1"].masked), 1)
        self.assertEqual(len(by_id["r2"].masked), 0)


# ---------------------------------------------------------- differential (对拍) tests

class DifferentialTests(unittest.TestCase):
    def check_differential(self, old, new, rules):
        raw = diff(old, new)
        res = apply_rules(raw, rules)

        # 1) kept ∪ masked == raw, disjoint
        self.assertEqual(keys(res.kept) | keys(res.masked), keys(raw))
        self.assertFalse(keys(res.kept) & keys(res.masked))

        # 2) with-rules result == without-rules result minus audited masked set
        self.assertEqual(keys(raw) - keys(res.kept), keys(res.masked))

        # 3) every structural diff survives
        structural = {d.key for d in raw if d.kind in STRUCTURAL_KINDS}
        self.assertTrue(structural <= keys(res.kept))

        # 4) audit masked union == global masked set
        audited = set()
        for a in res.audits:
            audited |= keys(a.masked)
        self.assertEqual(audited, keys(res.masked))

        # 5) cross-check against the INDEPENDENT naive matcher
        for d in raw:
            naive_hit = any(naive_rule_matches(r, d) for r in rules)
            if d.key in keys(res.masked):
                self.assertTrue(naive_hit, f"masked but naive says no: {d.render()}")
            elif d.kind == VALUE_CHANGED:
                self.assertFalse(naive_hit, f"kept but naive says mask: {d.render()}")

    def test_fixed_example(self):
        old = {
            "version": 1,
            "meta": {"updated_at": "2026-09-01T00:00:00Z", "note": "keep"},
            "servers": [{"host": "a", "port": 80}, {"host": "b", "port": 81}],
            "legacy": True,
        }
        new = {
            "version": 2,
            "meta": {"updated_at": "2026-09-28T08:00:00+08:00", "note": "keep"},
            "servers": [{"host": "a", "port": 8080}],
            "comment": "added later",
        }
        rules = [
            Rule(id="ts", type="iso8601"),
            Rule(id="ports", path="/servers/*/port"),
            Rule(id="dead", path="/no/such/path"),
        ]
        self.check_differential(old, new, rules)
        res = apply_rules(diff(old, new), rules)
        kept = {d.key for d in res.kept}
        # structural diffs must survive even though nothing ignores them
        self.assertIn((ITEM_REMOVED, "/servers/1"), kept)
        self.assertIn((KEY_REMOVED, "/legacy"), kept)
        self.assertIn((KEY_ADDED, "/comment"), kept)
        self.assertIn((VALUE_CHANGED, "/version"), kept)
        self.assertEqual(keys(res.masked), {(VALUE_CHANGED, "/meta/updated_at"),
                                            (VALUE_CHANGED, "/servers/0/port")})

    # ---------------- fuzz

    def random_value(self, rng, depth):
        if depth <= 0:
            return rng.choice([1, 2, 3, "x", "y", True, None, 1.5])
        kind = rng.randrange(4)
        if kind == 0:
            return {f"k{rng.randrange(4)}": self.random_value(rng, depth - 1)
                    for _ in range(rng.randrange(4))}
        if kind == 1:
            return [self.random_value(rng, depth - 1) for _ in range(rng.randrange(4))]
        return rng.choice([
            1, 2, 3, "x", "y", True, None, 1.5,
            "2026-09-28T10:00:00Z", "2026-01-01", "not-a-date",
        ])

    def random_rules(self, rng):
        pool = [
            {"id": "g1", "path": "/**"},
            {"id": "g2", "path": "/k0"},
            {"id": "g3", "path": "/k1/*"},
            {"id": "g4", "path": "k2"},
            {"id": "g5", "path": "/**/k3"},
            {"id": "t1", "type": "iso8601"},
            {"id": "t2", "type": "number"},
            {"id": "t3", "type": "string"},
            {"id": "t4", "type": "boolean"},
        ]
        rng.shuffle(pool)
        return [Rule(**e) for e in pool[: rng.randrange(len(pool) + 1)]]

    def test_fuzz_differential(self):
        rng = random.Random(20260928)
        for i in range(500):
            old = self.random_value(rng, depth=3)
            new = self.random_value(rng, depth=3)
            rules = self.random_rules(rng)
            with self.subTest(iteration=i, old=old, new=new,
                              rules=[r.id for r in rules]):
                self.check_differential(old, new, rules)


if __name__ == "__main__":
    unittest.main()
