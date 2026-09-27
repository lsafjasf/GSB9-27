"""End-to-end checks over a real loopback HTTP socket (stdlib only)."""

import http.client
import threading
import unittest

from condcache.http_app import make_server


class HttpIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.server = make_server()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address
        self.store = self.server.condcache_store
        self.rev = self.store.put("doc", b"payload over the wire", {"k": "v"})

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def _request(self, path, method="GET", headers=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        body = response.read()
        result = response.status, dict(response.getheaders()), body
        conn.close()
        return result

    def test_first_get_is_200_with_validators(self):
        status, headers, body = self._request("/doc")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"payload over the wire")
        self.assertEqual(headers["ETag"], self.rev.etag)
        self.assertIn("Last-Modified", headers)

    def test_etag_revalidation_over_http_is_304(self):
        status, headers, body = self._request(
            "/doc", headers={"If-None-Match": self.rev.etag}
        )
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        self.assertEqual(headers["ETag"], self.rev.etag)

    def test_stale_etag_after_update_is_200(self):
        self.store.put("doc", b"changed over the wire")
        status, _, body = self._request(
            "/doc", headers={"If-None-Match": self.rev.etag}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"changed over the wire")

    def test_last_modified_revalidation(self):
        status, _, body = self._request(
            "/doc", headers={"If-Modified-Since": self.rev.last_modified}
        )
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")

    def test_forged_headers_over_http_fail_closed(self):
        status, _, body = self._request(
            "/doc", headers={"If-None-Match": '"totally-forged"'}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"payload over the wire")

    def test_missing_resource_404(self):
        status, _, body = self._request("/missing")
        self.assertEqual(status, 404)
        self.assertEqual(body, b"Not Found")

    def test_head_has_headers_no_body(self):
        status, headers, body = self._request("/doc", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(int(headers["Content-Length"]), len(b"payload over the wire"))


if __name__ == "__main__":
    unittest.main()
