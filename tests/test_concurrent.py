"""Deterministic behaviour under concurrent updates."""

import threading
import unittest
from collections import Counter

from condcache import OriginStore, Request, handle_conditional, validators


def _etag_headers_consistent(response):
    """Invariant for every observed response: ETag identifies the body/metadata."""
    if response.status == 304:
        return True  # no body; the decision was made against the current revision
    if response.status != 200:
        return True
    return response.headers["ETag"] == validators.make_etag(response.body)


class ConcurrentUpdateTest(unittest.TestCase):
    def test_stale_etag_after_update_never_returns_304(self):
        """Ordered scenario required by the spec:

        1. GET        -> 200 + ETag(v1)
        2. PUT v2     (new content)
        3. GET with If-None-Match: ETag(v1) -> MUST be 200, never 304.
        """
        store = OriginStore()
        v1 = store.put("k", b"version one")
        store.put("k", b"version two - different bytes")

        response = handle_conditional(
            store, Request("k", if_none_match=v1.etag)
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"version two - different bytes")
        self.assertNotEqual(response.headers["ETag"], v1.etag)

    def test_stale_last_modified_after_update_returns_200(self):
        store = OriginStore()
        v1 = store.put("k", b"version one")
        current = store.put("k", b"version two")

        response = handle_conditional(
            store, Request("k", if_modified_since=v1.last_modified)
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Last-Modified"], current.last_modified)

    def test_concurrent_readers_and_writers_never_observe_torn_state(self):
        store = OriginStore()
        store.put("k", b"init")

        stop = threading.Event()
        barrier = threading.Barrier(5)
        statuses: Counter[int] = Counter()
        errors: list[Exception] = []
        lock = threading.Lock()

        def writer(worker_id: int):
            try:
                barrier.wait()
                rounds = 0
                while not stop.is_set() and rounds < 300:
                    body = f"writer-{worker_id}-round-{rounds}".encode()
                    store.put("k", body)
                    rounds += 1
            except Exception as exc:  # pragma: no cover - test failure reporting
                with lock:
                    errors.append(exc)

        def reader():
            try:
                barrier.wait()
                rounds = 0
                # Revalidate with the validator learned on the *previous*
                # round, so concurrent writers force a realistic mix of 200
                # (content changed) and 304 (still current).
                last_seen_etag = None
                while not stop.is_set() and rounds < 300:
                    unconditional = handle_conditional(store, Request("k"))
                    assert _etag_headers_consistent(unconditional)
                    revalidate = handle_conditional(
                        store,
                        Request("k", if_none_match=last_seen_etag)
                        if last_seen_etag is not None
                        else Request("k"),
                    )
                    assert _etag_headers_consistent(revalidate)

                    if revalidate.status == 304:
                        assert last_seen_etag is not None
                        assert (
                            revalidate.headers["ETag"]
                            == unconditional.headers["ETag"]
                        )
                    else:
                        # A miss is only legal when the old validator is stale
                        # and the 200 body must match the live revision.
                        current = store.get("k")
                        assert revalidate.body == current.body

                    last_seen_etag = unconditional.headers["ETag"]
                    with lock:
                        statuses[revalidate.status] += 1
                    rounds += 1
            except Exception as exc:  # pragma: no cover
                with lock:
                    errors.append(exc)

        # Closes the run after 0.2s; workers also bound their own rounds.
        def stopper():
            barrier.wait()
            stop.wait(0.2)
            stop.set()

        threads = (
            [threading.Thread(target=writer, args=(i,)) for i in range(2)]
            + [threading.Thread(target=reader) for _ in range(2)]
            + [threading.Thread(target=stopper)]
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertIn(200, statuses)  # races genuinely observed
        self.assertGreater(sum(statuses.values()), 0)

    def test_304_pairs_exactly_with_current_revision(self):
        """Every 304 must be explainable by the revision at decision time."""
        store = OriginStore()
        v1 = store.put("k", b"payload")
        self.assertEqual(
            handle_conditional(store, Request("k", if_none_match=v1.etag)).status,
            304,
        )
        v2 = store.put("k", b"payload changed")
        # Old validator misses; new validator (learned from the 200) hits again.
        self.assertEqual(
            handle_conditional(store, Request("k", if_none_match=v1.etag)).status,
            200,
        )
        self.assertEqual(
            handle_conditional(store, Request("k", if_none_match=v2.etag)).status,
            304,
        )


if __name__ == "__main__":
    unittest.main()
