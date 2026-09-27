"""把 ResourceStore 暴露为真实 HTTP 服务（仅标准库 http.server）。

GET    /r/<key>   支持 If-None-Match / If-Modified-Since -> 304
PUT    /r/<key>   支持 If-Match -> 412；写体为请求实体
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from conditional_cache import (
    STATUS_NOT_MODIFIED,
    STATUS_OK,
    ResourceStore,
)


def make_handler(store: ResourceStore):
    class ConditionalHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _key(self) -> str | None:
            if self.path.startswith("/r/") and len(self.path) > 3:
                return self.path[3:]
            return None

        def _send(self, status: int, headers: dict, body: bytes) -> None:
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:
            key = self._key()
            if key is None:
                self._send(404, {}, b"")
                return
            resp = store.conditional_get(
                key,
                if_none_match=self.headers.get("If-None-Match"),
                if_modified_since=self.headers.get("If-Modified-Since"),
            )
            self._send(resp.status, resp.headers, resp.body)

        def do_PUT(self) -> None:
            key = self._key()
            if key is None:
                self._send(404, {}, b"")
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            resp = store.write(
                key,
                body,
                content_type=self.headers.get("Content-Type", "application/octet-stream"),
                if_match=self.headers.get("If-Match"),
            )
            self._send(resp.status, resp.headers, resp.body)

        def log_message(self, *args) -> None:  # 静默
            pass

    return ConditionalHandler


def serve(store: ResourceStore, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(store))
    return server


if __name__ == "__main__":
    import threading
    import urllib.request

    store = ResourceStore()
    store.write("hello", b"hello conditional world")
    server = serve(store)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    # 首次请求：200 + 校验器
    resp = urllib.request.urlopen(f"{base}/r/hello")
    etag = resp.headers["ETag"]
    print("first :", resp.status, resp.headers["ETag"], resp.read())

    # 携带校验器：304
    req = urllib.request.Request(f"{base}/r/hello", headers={"If-None-Match": etag})
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError as exc:  # 304 被 urllib 视为“错误”
        print("cached:", exc.code, dict(exc.headers).get("ETag"))

    server.shutdown()
