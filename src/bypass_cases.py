"""Bypass case set.

Each case smuggles the unauthorized type `app_types.ShellCommand` past the
whitelist of the vulnerable implementation. Run:

    python3 src/bypass_cases.py

For every case the script shows:
  * VULNERABLE impl: bypass succeeds (ShellCommand gets constructed).
  * SECURE impl:     rejected during validation, with the offending path,
                     and NOTHING is constructed at all.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app_types
import vulnerable_deserializer
from secure_deserializer import SecureDeserializer, DeserializationError

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "whitelist.json")

BYPASS_CASES = [
    (
        "B1 nested structure smuggling",
        "unauthorized type hidden inside __args__ of a whitelisted type",
        {"__type__": "vector", "__args__": [
            1, {"__type__": "app_types.ShellCommand", "__args__": ["rm -rf /"]}]},
    ),
    (
        "B2 alias indirection",
        "alias 'shell' resolves to a non-whitelisted type after the check",
        {"__type__": "shell", "__args__": ["id"]},
    ),
    (
        "B3 custom constructor",
        "whitelisted type, arbitrary classmethod builds the unauthorized type",
        {"__type__": "user", "__constructor__": "from_legacy", "__args__": [
            {"type": "app_types.ShellCommand", "args": ["whoami"]}]},
    ),
    (
        "B4 container indirection",
        "unauthorized type smuggled via __items__ of a whitelisted container",
        {"__type__": "builtins.tuple", "__items__": [
            {"__type__": "app_types.ShellCommand", "__args__": ["cat /etc/passwd"]}]},
    ),
]


def main():
    secure = SecureDeserializer.from_config_file(CONFIG_PATH)
    all_ok = True
    for name, desc, doc in BYPASS_CASES:
        payload = json.dumps(doc)
        print(f"== {name}\n   vector: {desc}")

        app_types.reset_log()
        try:
            result = vulnerable_deserializer.loads(payload)
            smuggled = [e for e in app_types.CONSTRUCTED
                        if e[0] == "ShellCommand"]
            print(f"   vulnerable: BYPASSED -> constructed {smuggled}"
                  f" (result={result!r})")
            bypassed = bool(smuggled)
        except Exception as exc:  # noqa: BLE001
            print(f"   vulnerable: unexpectedly rejected: {exc}")
            bypassed = False

        app_types.reset_log()
        try:
            secure.loads(payload)
            print("   secure:     !!! NOT REJECTED — REGRESSION !!!")
            all_ok = False
        except DeserializationError as exc:
            constructed = list(app_types.CONSTRUCTED)
            print(f"   secure:     REJECTED at {exc}")
            print(f"               objects constructed before reject: {constructed}")
            if constructed:
                print("               !!! construction happened before "
                      "validation finished — REGRESSION !!!")
                all_ok = False
        if not bypassed:
            all_ok = False
        print()

    print("RESULT:", "OK — all bypasses work on vulnerable impl and are "
          "rejected by secure impl" if all_ok else "FAILURES PRESENT")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
