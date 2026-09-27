"""Bypass case suite for the deserialization whitelist.

Each case is run against:
  * vulnerable_deserializer -- expected to be BYPASSED (an unauthorized
    demoapp.evil object is constructed, recorded in evil.CONSTRUCTED);
  * secure_deserializer    -- expected to be REJECTED with a JSON-path
    pointing at the offending node.

Run:  python3 bypass_cases.py
Exit code 0 iff every bypass is rejected by the secure deserializer.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import vulnerable_deserializer
from demoapp import evil
from secure_deserializer import DeserializationError, SecureDeserializer, Whitelist

VULN_ALLOWED_TYPES = [
    "demoapp.models.User",
    "demoapp.models.Point",
    "demoapp.models.TypedList",
    "demoapp.models.Node",
]
VULN_ALLOWED_ALIASES = {
    "User": "demoapp.models.User",
    "Point": "demoapp.models.Point",
}

CASES = [
    {
        "name": "nested_smuggling",
        "description": "嵌套结构里夹带未授权类型（根节点是普通 JSON 对象）",
        "payload": {
            "user": {"$type": "demoapp.models.User",
                     "$state": {"name": "alice"}},
            "payload": {"$type": "demoapp.evil.Backdoor",
                        "$state": {"cmd": "id"}},
        },
    },
    {
        "name": "alias_pointer",
        "description": "payload 自带 $aliases，把可信别名指向未授权类型",
        "payload": {
            "$aliases": {"User": "demoapp.evil.Backdoor"},
            "$type": "User",
            "$state": {"name": "alice"},
        },
    },
    {
        "name": "custom_constructor",
        "description": "白名单类型 + payload 指定的自定义构造器",
        "payload": {
            "$type": "demoapp.models.User",
            "$constructor": "demoapp.evil.backdoor_factory",
            "$state": {"name": "alice"},
        },
    },
    {
        "name": "container_indirect",
        "description": "通过白名单容器 TypedList 间接构造未授权类型",
        "payload": {
            "$type": "demoapp.models.TypedList",
            "$state": {"items": [
                {"$type": "demoapp.models.Point", "$state": {"x": 1, "y": 2}},
                {"$type": "demoapp.evil.Backdoor", "$state": {}},
            ]},
        },
    },
]


def run_vulnerable(text):
    evil.CONSTRUCTED.clear()
    try:
        vulnerable_deserializer.loads(
            text, VULN_ALLOWED_TYPES, VULN_ALLOWED_ALIASES)
    except Exception as exc:  # noqa: BLE001 - reporting baseline behavior
        return f"blocked ({type(exc).__name__}: {exc})", False
    if evil.CONSTRUCTED:
        return f"BYPASSED -> constructed {evil.CONSTRUCTED}", True
    return "accepted (no evil object)", False


def run_secure(deserializer, text):
    evil.CONSTRUCTED.clear()
    try:
        deserializer.loads(text)
    except DeserializationError as exc:
        return f"REJECTED: {exc}", not evil.CONSTRUCTED
    return f"NOT REJECTED (constructed={evil.CONSTRUCTED}) -- FAILURE", False


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    whitelist = Whitelist.from_json_file(os.path.join(here, "whitelist.json"))
    deserializer = SecureDeserializer(whitelist)

    all_rejected = True
    for case in CASES:
        text = json.dumps(case["payload"])
        vuln_result, bypassed = run_vulnerable(text)
        secure_result, rejected = run_secure(deserializer, text)
        all_rejected = all_rejected and rejected
        print(f"[{case['name']}] {case['description']}")
        print(f"  vulnerable : {vuln_result}")
        print(f"  secure     : {secure_result}")
        assert bypassed, f"bypass case {case['name']} no longer bypasses " \
                         f"the vulnerable baseline -- update the fixture"
        print()

    if all_rejected:
        print("OK: all bypass cases are rejected by the secure deserializer.")
        return 0
    print("FAILURE: at least one bypass succeeded against the secure "
          "deserializer.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
