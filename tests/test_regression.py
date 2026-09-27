"""Regression tests for the hardened deserializer.

Run: python3 -m unittest discover -s tests -v   (from repo root)
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

import app_types
from secure_deserializer import (
    DeserializationError,
    PayloadTooLargeError,
    SecureDeserializer,
    ValidationError,
)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "whitelist.json")


def make_deserializer(**limit_overrides):
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        config = json.load(fh)
    config.setdefault("limits", {}).update(limit_overrides)
    return SecureDeserializer(config)


class LegitDocumentTests(unittest.TestCase):
    def setUp(self):
        app_types.reset_log()
        self.de = make_deserializer()

    def test_plain_scalars_and_containers(self):
        self.assertEqual(self.de.loads('{"a": [1, 2.5, "x", null, true]}'),
                         {"a": [1, 2.5, "x", None, True]})

    def test_whitelisted_type_via_alias(self):
        obj = self.de.loads('{"__type__": "vector", "__args__": [3, 4]}')
        self.assertIsInstance(obj, app_types.Vector)
        self.assertEqual((obj.x, obj.y), (3, 4))

    def test_whitelisted_custom_constructor(self):
        obj = self.de.loads(
            '{"__type__": "vector", "__constructor__": "from_pair",'
            ' "__args__": [[7, 8]]}')
        self.assertEqual((obj.x, obj.y), (7, 8))

    def test_nested_whitelisted_types(self):
        obj = self.de.loads(
            '{"__type__": "builtins.tuple", "__items__": ['
            ' {"__type__": "user", "__args__": ["alice"]},'
            ' {"__type__": "vector", "__args__": [1, 2]}]}')
        self.assertIsInstance(obj[0], app_types.User)
        self.assertIsInstance(obj[1], app_types.Vector)


class EmptyStructureTests(unittest.TestCase):
    def setUp(self):
        self.de = make_deserializer()

    def test_empty_object(self):
        self.assertEqual(self.de.loads("{}"), {})

    def test_empty_array(self):
        self.assertEqual(self.de.loads("[]"), [])

    def test_empty_string_and_null(self):
        self.assertEqual(self.de.loads('""'), "")
        self.assertEqual(self.de.loads("null"), None)

    def test_empty_args_typed_node(self):
        obj = self.de.loads('{"__type__": "vector"}')
        self.assertEqual((obj.x, obj.y), (0, 0))


class DeepNestingTests(unittest.TestCase):
    def test_deep_nesting_rejected_with_path(self):
        de = make_deserializer(max_depth=100)
        depth = 500
        payload = "[" * depth + "]" * depth
        with self.assertRaises(ValidationError) as ctx:
            de.loads(payload)
        self.assertIn("nesting depth exceeds 100", str(ctx.exception))

    def test_deep_nesting_within_limit_accepted(self):
        de = make_deserializer(max_depth=200)
        depth = 150
        payload = "[" * depth + "1" + "]" * depth
        obj = de.loads(payload)
        for _ in range(depth):
            obj = obj[0]
        self.assertEqual(obj, 1)


class CycleTests(unittest.TestCase):
    def test_cyclic_list_rejected(self):
        de = make_deserializer()
        doc = []
        doc.append(doc)
        with self.assertRaises(ValidationError) as ctx:
            de.load_obj(doc)
        self.assertIn("cyclic reference", str(ctx.exception))
        self.assertEqual(ctx.exception.path, "[0]")

    def test_cyclic_dict_rejected(self):
        de = make_deserializer()
        doc = {"self": None}
        doc["self"] = doc
        with self.assertRaises(ValidationError) as ctx:
            de.load_obj(doc)
        self.assertIn("cyclic reference", str(ctx.exception))

    def test_shared_reference_is_not_a_cycle(self):
        de = make_deserializer()
        shared = [1, 2]
        self.assertEqual(de.load_obj([shared, shared]), [[1, 2], [1, 2]])


class OversizedPayloadTests(unittest.TestCase):
    def test_payload_bytes_limit(self):
        de = make_deserializer(max_bytes=1024)
        payload = json.dumps({"data": "x" * 5000})
        with self.assertRaises(PayloadTooLargeError):
            de.loads(payload)

    def test_node_count_limit(self):
        de = make_deserializer(max_nodes=100)
        payload = json.dumps(list(range(500)))
        with self.assertRaises(ValidationError) as ctx:
            de.loads(payload)
        self.assertIn("node count exceeds 100", str(ctx.exception))


class BypassRejectionTests(unittest.TestCase):
    """Every bypass case from src/bypass_cases.py must be rejected,
    with a path, and with ZERO objects constructed."""

    CASES = {
        "B1 nested smuggling":
            {"__type__": "vector", "__args__": [
                1, {"__type__": "app_types.ShellCommand",
                    "__args__": ["rm -rf /"]}]},
        "B2 alias indirection":
            {"__type__": "shell", "__args__": ["id"]},
        "B3 custom constructor":
            {"__type__": "user", "__constructor__": "from_legacy",
             "__args__": [{"type": "app_types.ShellCommand",
                           "args": ["whoami"]}]},
        "B4 container indirection":
            {"__type__": "builtins.tuple", "__items__": [
                {"__type__": "app_types.ShellCommand",
                 "__args__": ["cat /etc/passwd"]}]},
    }

    def test_all_bypasses_rejected(self):
        de = make_deserializer()
        for name, doc in self.CASES.items():
            with self.subTest(case=name):
                app_types.reset_log()
                with self.assertRaises(DeserializationError) as ctx:
                    de.loads(json.dumps(doc))
                exc = ctx.exception
                self.assertTrue(getattr(exc, "path", None),
                                "rejection must carry a path")
                self.assertEqual(app_types.CONSTRUCTED, [],
                                 "no object may be constructed on reject")

    def test_rejection_paths_point_at_offender(self):
        de = make_deserializer()
        expectations = {
            "B1 nested smuggling": ".__args__[1]",
            "B2 alias indirection": "<root>",
            "B3 custom constructor": "<root>",
            "B4 container indirection": ".__items__[0]",
        }
        for name, doc in self.CASES.items():
            with self.subTest(case=name):
                with self.assertRaises(ValidationError) as ctx:
                    de.loads(json.dumps(doc))
                self.assertEqual(ctx.exception.path, expectations[name])

    def test_deeply_nested_smuggling_rejected(self):
        de = make_deserializer()
        doc = {"__type__": "app_types.ShellCommand", "__args__": ["x"]}
        for _ in range(10):
            doc = {"wrap": [doc]}
        with self.assertRaises(ValidationError) as ctx:
            de.load_obj(doc)
        self.assertIn("ShellCommand", str(ctx.exception))
        self.assertIn(".wrap[0]", ctx.exception.path)


class WhitelistPolicyTests(unittest.TestCase):
    def test_default_deny_unknown_type(self):
        de = make_deserializer()
        with self.assertRaises(ValidationError) as ctx:
            de.loads('{"__type__": "app_types.ShellCommand", "__args__": []}')
        self.assertIn("not in whitelist", str(ctx.exception))

    def test_alias_to_nonwhitelisted_type_rejected_at_config_time(self):
        config = {
            "default_policy": "deny",
            "types": {"app_types.Vector": {}},
            "aliases": {"shell": "app_types.ShellCommand"},
        }
        with self.assertRaises(ValueError):
            SecureDeserializer(config)

    def test_constructor_must_be_registered(self):
        de = make_deserializer()
        with self.assertRaises(ValidationError) as ctx:
            de.loads('{"__type__": "user", "__constructor__": "from_legacy",'
                     ' "__args__": [{}]}')
        self.assertIn("constructor 'from_legacy' not allowed",
                      str(ctx.exception))

    def test_new_type_requires_explicit_registration(self):
        # Not registered -> denied.
        de = make_deserializer()
        with self.assertRaises(ValidationError):
            de.loads('{"__type__": "app_types.ShellCommand", "__args__": []}')
        # Explicitly registered -> allowed.
        config = {
            "default_policy": "deny",
            "types": {"app_types.ShellCommand": {"constructors": ["__init__"]}},
        }
        de2 = SecureDeserializer(config)
        obj = de2.loads('{"__type__": "app_types.ShellCommand",'
                        ' "__args__": ["echo hi"]}')
        self.assertIsInstance(obj, app_types.ShellCommand)

    def test_non_deny_default_policy_refused(self):
        with self.assertRaises(ValueError):
            SecureDeserializer({"default_policy": "allow", "types": {}})


if __name__ == "__main__":
    unittest.main()
