"""Real HTTP/1.1 adapter using the standard-library ``http.server``.

Demonstrates that the library's conditional semantics survive an actual
HTTP wire round-trip (line endings, date parsing, ETag quoting included).
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer

from .origin import OriginStore
from .protocol import Request, handle_conditional
from .wire import response_bytes


class ConditionalHandler(BaseHTTPRequestHandler):
    server_version = "CondCache/1.0"

    @property
    def store(self) -> OriginStore:
        return self.server.condcache_store  # type: ignore[attr-defined]

    def _key(self) -> str:
        return self.path.lstrip("/").split("?", 1)[0]

    def _dispatch(self, method: str) -> None:
        request = Request(
            key=self._key(),
            method=method,
            if_none_match=self.headers.get("If-None-Match"),
            if_modified_since=self.headers.get("If-Modified-Since"),
        )
        response = handle_conditional(self.store, request)
        raw = response_bytes(response, include_body=(method != "HEAD"))
        self.wfile.write(raw)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def log_message(self, format, *args):  # silence test output
        pass


def make_server(host: str = "127.0.0.1", port: int = 0, store: OriginStore | None = None):
    server = HTTPServer((host, port), ConditionalHandler)
    server.condcache_store = store or OriginStore()
    return server
