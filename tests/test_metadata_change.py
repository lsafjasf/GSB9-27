"""Content identical but metadata changed: ETag stable, Last-Modified advances."""

import unittest

from condcache import OriginStore, Request, handle_conditional, validators


class Clock:
    def __init__(self, start=2_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


class MetadataChangeTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = OriginStore(clock=self.clock)
        self.v1 = self.store.put("doc", b"same bytes", {"author": "ada"})

    def test_touch_keeps_etag_and_body_changes_mtime(self):
        self.clock.now += 5
        v2 = self.store.touch("doc", {"author": "grace"})

        self.assertEqual(v2.etag, self.v1.etag)
        self.assertEqual(v2.body, self.v1.body)
        self.assertGreater(v2.mtime, self.v1.mtime)
        self.assertEqual(v2.metadata, {"author": "grace"})

    def test_etag_client_gets_304_and_refreshed_metadata(self):
        # Client revalidates with the content validator after metadata-only change.
        self.clock.now += 5
        self.store.touch("doc", {"author": "grace"})

        response = handle_conditional(
            self.store, Request("doc", if_none_match=self.v1.etag)
        )
        self.assertEqual(response.status, 304)
        self.assertEqual(response.headers["ETag"], self.v1.etag)
        self.assertEqual(response.headers["X-Meta-author"], "grace")

    def test_ims_client_detects_metadata_change_as_200(self):
        # A pure Last-Modified cache cannot distinguish content from metadata
        # changes: the timestamp advanced, so it downloads again.
        self.clock.now += 5
        v2 = self.store.touch("doc", {"author": "grace"})

        response = handle_conditional(
            self.store, Request("doc", if_modified_since=self.v1.last_modified)
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"same bytes")
        self.assertEqual(response.headers["Last-Modified"], v2.last_modified)
        self.assertEqual(response.headers["X-Meta-author"], "grace")

    def test_ims_with_fresh_date_still_gets_304_after_touch(self):
        self.clock.now += 5
        v2 = self.store.touch("doc", {"author": "grace"})
        response = handle_conditional(
            self.store, Request("doc", if_modified_since=v2.last_modified)
        )
        self.assertEqual(response.status, 304)

    def test_same_second_write_still_makes_ims_observable(self):
        # Monotonic mtime guarantee: even without wall-clock progress the
        # new revision is strictly newer than the previous one.
        v2 = self.store.put("doc", b"same bytes", {"author": "grace"})
        self.assertGreater(v2.mtime, self.v1.mtime)
        response = handle_conditional(
            self.store, Request("doc", if_modified_since=self.v1.last_modified)
        )
        self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
