"""Status semantics: first request, ETag/Last-Modified hits, forged validators."""

import time
import unittest

from condcache import OriginStore, Request, handle_conditional, handle_full, validators


class Clock:
    def __init__(self, start=1_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


class StatusSemanticsTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = OriginStore(clock=self.clock)
        self.rev = self.store.put("a", b"hello world", {"author": "ada"})
        self.etag = self.rev.etag
        self.lm = self.rev.last_modified

    # --- first request -------------------------------------------------
    def test_first_request_no_validator_is_full_200(self):
        response = handle_conditional(self.store, Request("a"))
        self.assertEqual(response.status, 200)
        self.assertFalse(response.not_modified)
        self.assertEqual(response.body, b"hello world")
        self.assertEqual(response.headers["ETag"], self.etag)
        self.assertEqual(response.headers["Last-Modified"], self.lm)
        self.assertEqual(response.headers["X-Meta-author"], "ada")

    def test_missing_resource_is_404(self):
        self.assertEqual(handle_conditional(self.store, Request("nope")).status, 404)

    # --- ETag revalidation ---------------------------------------------
    def test_if_none_match_hit_returns_304_empty_body(self):
        request = Request("a", if_none_match=self.etag)
        response = handle_conditional(self.store, request)
        self.assertEqual(response.status, 304)
        self.assertTrue(response.not_modified)
        self.assertEqual(response.body, b"")
        # 304 still carries current validators and metadata.
        self.assertEqual(response.headers["ETag"], self.etag)
        self.assertEqual(response.headers["Last-Modified"], self.lm)
        self.assertEqual(response.headers["X-Meta-author"], "ada")

    def test_wildcard_matches_existing_resource(self):
        response = handle_conditional(
            self.store, Request("a", if_none_match="*")
        )
        self.assertEqual(response.status, 304)

    def test_weak_etag_compares_weakly(self):
        response = handle_conditional(
            self.store, Request("a", if_none_match="W/" + self.etag)
        )
        self.assertEqual(response.status, 304)

    # --- Last-Modified revalidation ------------------------------------
    def test_if_modified_since_equal_instant_is_304(self):
        request = Request("a", if_modified_since=self.lm)
        self.assertEqual(handle_conditional(self.store, request).status, 304)

    def test_if_modified_since_later_instant_is_304(self):
        future = validators.http_date(self.clock.now + 10)
        request = Request("a", if_modified_since=future)
        self.assertEqual(handle_conditional(self.store, request).status, 304)

    def test_if_modified_since_older_instant_is_200(self):
        past = validators.http_date(self.clock.now - 10)
        request = Request("a", if_modified_since=past)
        response = handle_conditional(self.store, request)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"hello world")

    # --- precedence -----------------------------------------------------
    def test_if_none_match_takes_precedence_over_ims(self):
        # ETag does not match but the date would -> must be 200, not 304.
        request = Request(
            "a",
            if_none_match='"deadbeef-deadbeef"',
            if_modified_since=validators.http_date(self.clock.now + 100),
        )
        response = handle_conditional(self.store, request)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"hello world")

    # --- forged / malformed validators ----------------------------------
    def test_forged_etag_garbage_does_not_hit(self):
        for forged in ['"not-a-hash"', "W/123", "no-quotes", '"" "extra"', "", "💥"]:
            with self.subTest(forged=forged):
                response = handle_conditional(
                    self.store, Request("a", if_none_match=forged)
                )
                self.assertEqual(response.status, 200)
                self.assertEqual(response.body, b"hello world")

    def test_etag_of_other_resource_does_not_hit(self):
        other = self.store.put("b", b"different bytes")
        response = handle_conditional(
            self.store, Request("a", if_none_match=other.etag)
        )
        self.assertEqual(response.status, 200)

    def test_malformed_list_member_invalidates_header(self):
        response = handle_conditional(
            self.store,
            Request("a", if_none_match=f"{self.etag}, garbage-tag"),
        )
        self.assertEqual(response.status, 200)

    def test_malformed_if_modified_since_is_ignored(self):
        for forged in ("not-a-date", "13", "Sun, 32 Jan 2020 99:99:99 GMT"):
            with self.subTest(forged=forged):
                response = handle_conditional(
                    self.store, Request("a", if_modified_since=forged)
                )
                self.assertEqual(response.status, 200)

    # --- method semantics -----------------------------------------------
    def test_head_full_response_has_no_body_but_content_length(self):
        response = handle_conditional(self.store, Request("a", method="HEAD"))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"")
        self.assertEqual(response.headers["Content-Length"], "11")

    def test_head_revalidation_can_return_304(self):
        response = handle_conditional(
            self.store, Request("a", method="HEAD", if_none_match=self.etag)
        )
        self.assertEqual(response.status, 304)

    def test_other_methods_rejected(self):
        self.assertEqual(
            handle_conditional(self.store, Request("a", method="POST")).status, 405
        )


if __name__ == "__main__":
    unittest.main()
