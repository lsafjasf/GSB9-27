"""Differential test: conditional responses vs naive full responses.

For every GET in a shared request sequence:

* the *effective content* of the conditional client (200 body, or its
  cached body after a 304) must be byte-identical to the naive client's
  always-full body;
* every 200 response must be byte-identical (body + validator headers) to
  the naive response; every 304 must carry the current revision's
  validators and an empty body.
"""

import random
import unittest

from condcache import CachingClient, NaiveClient, OriginStore
from condcache.protocol import Request, handle_conditional, handle_full

PAYLOADS = [
    b"",
    b"a",
    b"hello world",
    b"\x00\x01\x02 binary \xff\xfe",
    "中文内容 — café".encode(),
    b"x" * 4096,
]


class ScriptRunner:
    """Runs one operation sequence against two caches on one origin."""

    def __init__(self, seed=None):
        self.store = OriginStore()
        self.naive = NaiveClient(self.store)
        self.conditional = CachingClient(self.store)
        self.random = random.Random(seed)
        self.events = []  # (step, status, key)

    def put(self, key, body, metadata=None):
        self.store.put(key, body, metadata)

    def touch(self, key, metadata):
        self.store.touch(key, metadata)

    def get(self, step, key):
        # Response-level cross-check on the current revision.
        full = handle_full(self.store, Request(key))
        cached = self.conditional._cache.get(key)
        conditional = handle_conditional(
            self.store,
            Request(
                key,
                if_none_match=cached.etag if cached else None,
                if_modified_since=cached.last_modified if cached else None,
            ),
        )

        if conditional.status == 200:
            assert conditional.body == full.body
            assert conditional.headers["ETag"] == full.headers["ETag"]
            assert (
                conditional.headers["Last-Modified"]
                == full.headers["Last-Modified"]
            )
            for name, value in full.headers.items():
                if name.startswith("X-Meta-"):
                    assert conditional.headers[name] == value
        else:
            assert conditional.status == 304
            assert conditional.body == b""
            assert conditional.headers["ETag"] == full.headers["ETag"]
            assert (
                conditional.headers["Last-Modified"]
                == full.headers["Last-Modified"]
            )

        # Client-level effective content must be byte-identical.
        naive_bytes = self.naive.get(key)
        effective_bytes = self.conditional.get(key)
        assert naive_bytes == effective_bytes
        assert isinstance(effective_bytes, bytes)
        self.events.append((step, conditional.status, key))


class DifferentialTest(unittest.TestCase):
    def _runner(self, seed=None):
        return ScriptRunner(seed)

    def test_fixed_sequence_covers_all_cases(self):
        runner = self._runner()
        runner.put("k1", b"v0", {"v": "0"})
        runner.get(0, "k1")          # first request, no validator
        runner.get(1, "k1")          # 304 hit
        runner.put("k1", b"v1", {"v": "1"})
        runner.get(2, "k1")          # new content -> full 200
        runner.get(3, "k1")          # 304
        runner.touch("k1", {"v": "1m"})
        runner.get(4, "k1")          # same content: ETag client gets 304
        runner.put("k1", b"v1", {"v": "1b"})  # same bytes, newer mtime
        runner.get(5, "k1")
        runner.put("k2", b"shared")
        runner.put("k3", b"")
        runner.get(6, "k2")
        runner.get(7, "k3")          # empty body still revalidates
        runner.get(8, "k3")

        statuses = [status for _, status, _ in runner.events]
        self.assertEqual(statuses[0], 200)
        self.assertEqual(statuses[1], 304)
        self.assertEqual(statuses[2], 200)
        self.assertEqual(statuses[3], 304)
        self.assertEqual(statuses[4], 304)  # metadata-only change
        self.assertIn(304, statuses)

    def test_randomized_sequences(self):
        observed = {200: 0, 304: 0}
        for seed in range(40):
            runner = self._runner(seed)
            rng = runner.random
            keys = [f"k{i}" for i in range(5)]
            for key in keys:
                runner.put(key, rng.choice(PAYLOADS), {"rev": "0"})
            for step in range(120):
                key = rng.choice(keys)
                action = rng.random()
                if action < 0.35:
                    runner.put(key, rng.choice(PAYLOADS), {"rev": str(step)})
                elif action < 0.5:
                    runner.touch(key, {"rev": f"m{step}"})
                else:
                    runner.get(step, key)
            for _, status, _ in runner.events:
                observed[status] += 1
        self.assertGreater(observed[200], 0)
        self.assertGreater(observed[304], 0)

    def test_empty_body_etag_is_stable(self):
        store = OriginStore()
        a = store.put("e", b"")
        b = store.put("e", b"")
        self.assertEqual(a.etag, b.etag)
        # Same bytes after a "write" -> 304 is correct and content is b"".
        response = handle_conditional(store, Request("e", if_none_match=a.etag))
        self.assertEqual(response.status, 304)
        naive = handle_full(store, Request("e"))
        self.assertEqual(naive.body, b"")


if __name__ == "__main__":
    unittest.main()
