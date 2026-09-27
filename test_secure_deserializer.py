"""Regression tests for the secure deserializer.

Run:  python3 -m unittest test_secure_deserializer -v
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bypass_cases import CASES, VULN_ALLOWED_TYPES, VULN_ALLOWED_ALIASES
import vulnerable_deserializer
from demoapp import evil
from demoapp.models import Node, Point, TypedList, User
from secure_deserializer import (
    DeserializationError,
    SecureDeserializer,
    TypeRule,
    Whitelist,
)

HERE = os.path.dirname(os.path.abspath(__file__))


def make_deserializer(**kwargs):
    whitelist = Whitelist.from_json_file(os.path.join(HERE, "whitelist.json"))
    return SecureDeserializer(whitelist, **kwargs)


class BypassRejectedTests(unittest.TestCase):
    """Every bypass case from the suite must be rejected, with a path,
    before anything is constructed."""

    def setUp(self):
        self.deserializer = make_deserializer()

    def test_all_bypass_cases_rejected(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                text = json.dumps(case["payload"])
                evil.CONSTRUCTED.clear()
                with self.assertRaises(DeserializationError) as ctx:
                    self.deserializer.loads(text)
                self.assertTrue(ctx.exception.path.startswith("$"))
                self.assertEqual(evil.CONSTRUCTED, [],
                                 "unauthorized object was constructed")

    def test_bypass_cases_actually_bypass_vulnerable_baseline(self):
        # Guards the fixtures: each case must still defeat the old code.
        for case in CASES:
            with self.subTest(case=case["name"]):
                text = json.dumps(case["payload"])
                evil.CONSTRUCTED.clear()
                vulnerable_deserializer.loads(
                    text, VULN_ALLOWED_TYPES, VULN_ALLOWED_ALIASES)
                self.assertNotEqual(evil.CONSTRUCTED, [],
                                    "fixture no longer bypasses baseline")

    def test_validation_happens_before_any_construction(self):
        payload = json.dumps({
            "$type": "demoapp.models.TypedList",
            "$state": {"items": [
                {"$type": "demoapp.models.User", "$state": {"name": "a"}},
                {"$type": "demoapp.evil.Backdoor", "$state": {}},
            ]},
        })
        with mock.patch.object(SecureDeserializer, "_build",
                               side_effect=AssertionError(
                                   "construction started before validation "
                                   "finished")):
            with self.assertRaises(DeserializationError):
                self.deserializer.loads(payload)

    def test_nested_rejection_reports_path(self):
        payload = json.dumps({
            "$type": "demoapp.models.TypedList",
            "$state": {"items": [
                {"$type": "demoapp.models.Point", "$state": {"x": 1}},
                {"$type": "demoapp.evil.Backdoor", "$state": {}},
            ]},
        })
        with self.assertRaises(DeserializationError) as ctx:
            self.deserializer.loads(payload)
        self.assertEqual(ctx.exception.path, "$.$state.items[1].$type")
        self.assertIn("demoapp.evil.Backdoor", str(ctx.exception))


class WhitelistPolicyTests(unittest.TestCase):
    def setUp(self):
        self.deserializer = make_deserializer()

    def test_default_deny_unregistered_type(self):
        with self.assertRaises(DeserializationError) as ctx:
            self.deserializer.loads(
                '{"$type": "demoapp.evil.Backdoor", "$state": {}}')
        self.assertIn("not whitelisted", str(ctx.exception))
        self.assertEqual(ctx.exception.path, "$.$type")

    def test_alias_to_unregistered_type_rejected(self):
        whitelist = Whitelist(
            types={"demoapp.models.Point": TypeRule("demoapp.models.Point")},
            aliases={"Evil": "demoapp.evil.Backdoor"},
        )
        deserializer = SecureDeserializer(whitelist)
        with self.assertRaises(DeserializationError) as ctx:
            deserializer.loads('{"$type": "Evil", "$state": {}}')
        self.assertIn("non-whitelisted", str(ctx.exception))

    def test_payload_defined_aliases_rejected(self):
        with self.assertRaises(DeserializationError) as ctx:
            self.deserializer.loads(json.dumps({
                "$aliases": {"User": "demoapp.evil.Backdoor"},
                "$type": "User",
                "$state": {},
            }))
        self.assertIn("forbidden keys", str(ctx.exception))

    def test_payload_defined_constructor_rejected(self):
        with self.assertRaises(DeserializationError) as ctx:
            self.deserializer.loads(json.dumps({
                "$type": "demoapp.models.User",
                "$constructor": "demoapp.evil.backdoor_factory",
                "$state": {"name": "x"},
            }))
        self.assertIn("forbidden keys", str(ctx.exception))

    def test_reserved_dollar_keys_rejected_in_plain_objects(self):
        with self.assertRaises(DeserializationError) as ctx:
            self.deserializer.loads('{"$weird": 1}')
        self.assertIn("reserved key", str(ctx.exception))

    def test_unknown_field_rejected(self):
        with self.assertRaises(DeserializationError) as ctx:
            self.deserializer.loads(json.dumps({
                "$type": "demoapp.models.User",
                "$state": {"name": "a", "is_admin": True},
            }))
        self.assertIn("fields not allowed", str(ctx.exception))

    def test_field_kind_enforced(self):
        with self.assertRaises(DeserializationError) as ctx:
            self.deserializer.loads(json.dumps({
                "$type": "demoapp.models.User",
                "$state": {"name": "a", "age": "not-a-number"},
            }))
        self.assertIn("expects int", str(ctx.exception))


class EdgeCaseTests(unittest.TestCase):
    def setUp(self):
        self.deserializer = make_deserializer()

    def test_empty_and_plain_structures(self):
        self.assertEqual(self.deserializer.loads("{}"), {})
        self.assertEqual(self.deserializer.loads("[]"), [])
        self.assertIsNone(self.deserializer.loads("null"))
        self.assertEqual(self.deserializer.loads('{"a": [1, 2, "x"]}'),
                         {"a": [1, 2, "x"]})
        point = self.deserializer.loads(
            '{"$type": "demoapp.models.Point", "$state": {}}')
        self.assertEqual(point, Point(0, 0))

    def test_deep_nesting_rejected_by_max_depth(self):
        depth = 500
        payload = "[" * depth + "1" + "]" * depth
        with self.assertRaises(DeserializationError) as ctx:
            self.deserializer.loads(payload)
        self.assertIn("max_depth", str(ctx.exception))

    def test_extreme_nesting_rejected_gracefully(self):
        depth = 100_000
        payload = "[" * depth + "]" * depth
        with self.assertRaises(DeserializationError):
            self.deserializer.loads(payload)

    def test_oversized_payload_rejected(self):
        deserializer = make_deserializer(max_bytes=64)
        payload = json.dumps({"data": "x" * 1000})
        with self.assertRaises(DeserializationError) as ctx:
            deserializer.loads(payload)
        self.assertIn("max_bytes", str(ctx.exception))

    def test_max_nodes_rejected(self):
        deserializer = make_deserializer(max_nodes=10)
        payload = json.dumps(list(range(50)))
        with self.assertRaises(DeserializationError) as ctx:
            deserializer.loads(payload)
        self.assertIn("max_nodes", str(ctx.exception))

    def test_circular_reference_rejected(self):
        payload = json.dumps({
            "$id": "a",
            "$type": "demoapp.models.Node",
            "$state": {"name": "n", "next": {"$ref": "a"}},
        })
        with self.assertRaises(DeserializationError) as ctx:
            self.deserializer.loads(payload)
        self.assertIn("circular reference", str(ctx.exception))
        self.assertEqual(ctx.exception.path, "$.$state.next.$ref")

    def test_indirect_cycle_rejected(self):
        payload = json.dumps({
            "$id": "a",
            "$type": "demoapp.models.Node",
            "$state": {"name": "a", "next": {
                "$id": "b",
                "$type": "demoapp.models.Node",
                "$state": {"name": "b", "next": {"$ref": "a"}},
            }},
        })
        with self.assertRaises(DeserializationError) as ctx:
            self.deserializer.loads(payload)
        self.assertIn("circular reference", str(ctx.exception))

    def test_forward_reference_rejected(self):
        payload = json.dumps([
            {"$ref": "later"},
            {"$id": "later", "$type": "demoapp.models.Point",
             "$state": {"x": 1}},
        ])
        with self.assertRaises(DeserializationError) as ctx:
            self.deserializer.loads(payload)
        self.assertIn("unknown $ref id", str(ctx.exception))

    def test_shared_reference_allowed_and_identity_preserved(self):
        payload = json.dumps({
            "$type": "demoapp.models.TypedList",
            "$state": {"items": [
                {"$id": "u", "$type": "demoapp.models.User",
                 "$state": {"name": "alice"}},
                {"$ref": "u"},
            ]},
        })
        result = self.deserializer.loads(payload)
        self.assertIs(result.items[0], result.items[1])
        self.assertEqual(result.items[0].name, "alice")


class LegitimateUseTests(unittest.TestCase):
    def setUp(self):
        self.deserializer = make_deserializer()

    def test_round_trip_registered_types(self):
        user = self.deserializer.loads(json.dumps({
            "$type": "User",
            "$state": {"name": "alice", "age": 30},
        }))
        self.assertEqual(user, User("alice", 30))

    def test_registered_constructor_is_used(self):
        with mock.patch.object(User, "from_dict",
                               wraps=User.from_dict) as spy:
            self.deserializer.loads(json.dumps({
                "$type": "demoapp.models.User",
                "$state": {"name": "bob"},
            }))
        spy.assert_called_once_with(name="bob")

    def test_nested_whitelisted_structure(self):
        result = self.deserializer.loads(json.dumps({
            "$type": "demoapp.models.TypedList",
            "$state": {"items": [
                {"$type": "Point", "$state": {"x": 1, "y": 2}},
                {"plain": ["data", 42]},
            ]},
        }))
        self.assertIsInstance(result, TypedList)
        self.assertEqual(result.items[0], Point(1, 2))
        self.assertEqual(result.items[1], {"plain": ["data", 42]})

    def test_node_chain(self):
        result = self.deserializer.loads(json.dumps({
            "$type": "demoapp.models.Node",
            "$state": {"name": "head", "next": {
                "$type": "demoapp.models.Node",
                "$state": {"name": "tail"},
            }},
        }))
        self.assertIsInstance(result.next, Node)
        self.assertEqual(result.next.name, "tail")


if __name__ == "__main__":
    unittest.main()
