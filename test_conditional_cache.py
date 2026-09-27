"""条件请求与缓存校验测试套件。

运行：python3 -m unittest test_conditional_cache -v
"""

from __future__ import annotations

import http.client
import random
import threading
import unittest

from conditional_cache import (
    STATUS_NOT_FOUND,
    STATUS_NOT_MODIFIED,
    STATUS_OK,
    STATUS_PRECONDITION_FAILED,
    CachingClient,
    ResourceStore,
    http_date,
    make_etag,
)
from http_server import serve


class FakeClock:
    def __init__(self, start: float = 1_700_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float = 1.0) -> None:
        self.now += seconds


class StateSemanticsTest(unittest.TestCase):
    """状态语义：200 / 304 / 404 / 412 的精确行为。"""

    def setUp(self):
        self.clock = FakeClock()
        self.store = ResourceStore(clock=self.clock)
        self.store.write("doc", b"version-1", content_type="text/plain")

    def test_first_request_without_validator_returns_200_with_validators(self):
        resp = self.store.conditional_get("doc")
        self.assertEqual(resp.status, STATUS_OK)
        self.assertEqual(resp.body, b"version-1")
        self.assertEqual(resp.headers["ETag"], make_etag(b"version-1"))
        self.assertIn("Last-Modified", resp.headers)
        self.assertEqual(resp.headers["Content-Length"], "9")

    def test_matching_etag_returns_304_without_body(self):
        etag = self.store.snapshot("doc").etag
        resp = self.store.conditional_get("doc", if_none_match=etag)
        self.assertEqual(resp.status, STATUS_NOT_MODIFIED)
        self.assertEqual(resp.body, b"")
        # 304 仍须回带校验器，供客户端刷新缓存条目
        self.assertEqual(resp.headers["ETag"], etag)
        self.assertIn("Last-Modified", resp.headers)

    def test_matching_last_modified_returns_304(self):
        last_modified = http_date(self.store.snapshot("doc").last_modified)
        resp = self.store.conditional_get("doc", if_modified_since=last_modified)
        self.assertEqual(resp.status, STATUS_NOT_MODIFIED)

    def test_if_none_match_takes_precedence_over_if_modified_since(self):
        # 过期 ETag + 新鲜的日期：按 RFC 9110，If-None-Match 优先 => 200
        self.clock.advance(10)
        self.store.write("doc", b"version-2")
        fresh_date = http_date(self.clock.now + 3600)
        resp = self.store.conditional_get(
            "doc", if_none_match=make_etag(b"version-1"), if_modified_since=fresh_date
        )
        self.assertEqual(resp.status, STATUS_OK)
        self.assertEqual(resp.body, b"version-2")

    def test_forged_etag_returns_full_200(self):
        for forged in ('"deadbeef"', '"sha256-' + "0" * 64 + '"', "W/\"weak\"", "garbage"):
            resp = self.store.conditional_get("doc", if_none_match=forged)
            self.assertEqual(resp.status, STATUS_OK, forged)
            self.assertEqual(resp.body, b"version-1")

    def test_unparseable_modified_since_returns_200(self):
        resp = self.store.conditional_get("doc", if_modified_since="not-a-date")
        self.assertEqual(resp.status, STATUS_OK)

    def test_old_validator_after_write_never_gets_304(self):
        old_etag = self.store.snapshot("doc").etag
        old_date = http_date(self.store.snapshot("doc").last_modified)
        self.clock.advance(10)
        self.store.write("doc", b"version-2")
        resp = self.store.conditional_get(
            "doc", if_none_match=old_etag, if_modified_since=old_date
        )
        self.assertEqual(resp.status, STATUS_OK)
        self.assertEqual(resp.body, b"version-2")
        self.assertNotEqual(resp.headers["ETag"], old_etag)

    def test_same_content_metadata_change_keeps_etag_and_304(self):
        etag_before = self.store.snapshot("doc").etag
        self.clock.advance(5)
        self.assertTrue(self.store.touch_metadata("doc", owner="alice", version=3))
        self.assertEqual(self.store.snapshot("doc").etag, etag_before)
        resp = self.store.conditional_get("doc", if_none_match=etag_before)
        self.assertEqual(resp.status, STATUS_NOT_MODIFIED)
        self.assertEqual(self.store.snapshot("doc").metadata["owner"], "alice")

    def test_rewrite_identical_content_still_304(self):
        etag = self.store.snapshot("doc").etag
        self.clock.advance(5)
        self.store.write("doc", b"version-1")  # 内容未变
        resp = self.store.conditional_get("doc", if_none_match=etag)
        self.assertEqual(resp.status, STATUS_NOT_MODIFIED)

    def test_missing_resource_returns_404(self):
        self.assertEqual(self.store.conditional_get("nope").status, STATUS_NOT_FOUND)
        self.assertEqual(self.store.get("nope").status, STATUS_NOT_FOUND)

    def test_if_none_match_star_and_list(self):
        self.assertEqual(
            self.store.conditional_get("doc", if_none_match="*").status,
            STATUS_NOT_MODIFIED,
        )
        etag = self.store.snapshot("doc").etag
        listed = f'"other", {etag}, "third"'
        self.assertEqual(
            self.store.conditional_get("doc", if_none_match=listed).status,
            STATUS_NOT_MODIFIED,
        )

    def test_if_match_write_precondition(self):
        etag_v1 = self.store.snapshot("doc").etag
        # 前置条件满足：写入成功
        resp = self.store.write("doc", b"version-2", if_match=etag_v1)
        self.assertEqual(resp.status, STATUS_OK)
        # 前置条件过期（并发写冲突）：412 且内容不被覆盖
        resp = self.store.write("doc", b"evil", if_match=etag_v1)
        self.assertEqual(resp.status, STATUS_PRECONDITION_FAILED)
        self.assertEqual(self.store.get("doc").body, b"version-2")

    def test_etag_is_strong_and_content_derived(self):
        self.assertTrue(make_etag(b"x").startswith('"sha256-'))
        self.assertNotEqual(make_etag(b"a"), make_etag(b"b"))


class DifferentialTest(unittest.TestCase):
    """对拍：同一请求序列，条件请求结果与朴素全量响应字节一致。"""

    def test_random_workload_matches_naive(self):
        rng = random.Random(20260928)
        clock = FakeClock()
        store = ResourceStore(clock=clock)
        conditional = CachingClient(store, conditional=True)
        naive = CachingClient(store, conditional=False)

        keys = [f"res-{i}" for i in range(8)]
        for key in keys:
            store.write(key, rng.randbytes(64))

        reads = writes = 0
        for step in range(3000):
            clock.advance(rng.random())
            key = rng.choice(keys)
            if rng.random() < 0.15:
                if rng.random() < 0.2:
                    store.touch_metadata(key, tick=step)  # 只动元数据
                else:
                    store.write(key, rng.randbytes(rng.choice([16, 64, 512])))
                writes += 1
            else:
                got = conditional.fetch(key)
                want = naive.fetch(key)
                self.assertEqual(got, want, f"step={step} key={key}")
                self.assertIsInstance(got, bytes)
                reads += 1

        self.assertGreater(reads, 2000)
        self.assertGreater(writes, 100)
        # 条件请求必须显著省带宽
        self.assertLess(conditional.bytes_received, naive.bytes_received * 0.5)


class ConcurrencyTest(unittest.TestCase):
    """并发更新：行为确定，旧校验器永远拿不到 304。"""

    def test_write_between_cache_and_revalidate_is_deterministic(self):
        clock = FakeClock()
        store = ResourceStore(clock=clock)
        store.write("k", b"v1")
        stale_etag = store.snapshot("k").etag

        writer_done = threading.Event()
        release_reader = threading.Event()

        def writer():
            clock.advance(1)
            store.write("k", b"v2")
            writer_done.set()
            release_reader.wait(5)

        t = threading.Thread(target=writer)
        t.start()
        writer_done.wait(5)  # 确保写入已完成后才校验
        resp = store.conditional_get("k", if_none_match=stale_etag)
        release_reader.set()
        t.join()
        self.assertEqual(resp.status, STATUS_OK)
        self.assertEqual(resp.body, b"v2")

    def test_concurrent_readers_never_validate_stale_content(self):
        store = ResourceStore()
        store.write("k", b"gen-0")
        stop = threading.Event()
        violations: list[str] = []
        counts = {"ok": 0, "not_modified": 0}
        counts_lock = threading.Lock()

        def writer():
            gen = 1
            while not stop.is_set():
                store.write("k", f"gen-{gen}".encode())
                gen += 1

        def reader():
            cached_body: bytes | None = None
            cached_etag: str | None = None
            while not stop.is_set():
                resp = store.conditional_get("k", if_none_match=cached_etag)
                with counts_lock:
                    if resp.status == STATUS_NOT_MODIFIED:
                        counts["not_modified"] += 1
                        # 不变式：304 意味着此刻服务端内容的校验器
                        # 与本地缓存内容的校验器一致（ETag 由内容派生，
                        # 校验器相同 <=> 字节相同）。
                        if cached_body is None or resp.headers["ETag"] != make_etag(cached_body):
                            violations.append("stale 304")
                    elif resp.status == STATUS_OK:
                        counts["ok"] += 1
                        if resp.headers["ETag"] != make_etag(resp.body):
                            violations.append("etag/body mismatch")
                        cached_body, cached_etag = resp.body, resp.headers["ETag"]

        writers = [threading.Thread(target=writer) for _ in range(2)]
        readers = [threading.Thread(target=reader) for _ in range(4)]
        for t in writers + readers:
            t.start()
        import time

        time.sleep(1.0)
        stop.set()
        for t in writers + readers:
            t.join()

        self.assertEqual(violations, [])
        self.assertGreater(counts["not_modified"], 0, "应实际命中过 304 路径")
        self.assertGreater(counts["ok"], 0)


class HttpIntegrationTest(unittest.TestCase):
    """真实 HTTP 层：304/412 状态语义在线上成立。"""

    @classmethod
    def setUpClass(cls):
        cls.store = ResourceStore()
        cls.store.write("hello", b"hello-v1")
        cls.server = serve(cls.store)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _conn(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def test_full_conditional_roundtrip(self):
        conn = self._conn()
        conn.request("GET", "/r/hello")
        resp = conn.getresponse()
        body = resp.read()
        self.assertEqual(resp.status, 200)
        self.assertEqual(body, b"hello-v1")
        etag = resp.getheader("ETag")
        last_modified = resp.getheader("Last-Modified")
        conn.close()

        for headers in ({"If-None-Match": etag}, {"If-Modified-Since": last_modified}):
            conn = self._conn()
            conn.request("GET", "/r/hello", headers=headers)
            resp = conn.getresponse()
            self.assertEqual(resp.status, 304)
            self.assertEqual(resp.read(), b"")
            conn.close()

        # 服务端更新后，旧校验器在线上同样失效
        conn = self._conn()
        conn.request("PUT", "/r/hello", body=b"hello-v2")
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()

        conn = self._conn()
        conn.request("GET", "/r/hello", headers={"If-None-Match": etag})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.read(), b"hello-v2")
        conn.close()

    def test_if_match_conflict_over_http(self):
        conn = self._conn()
        conn.request("PUT", "/r/hello", body=b"x", headers={"If-Match": '"bogus"'})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 412)
        resp.read()
        conn.close()


if __name__ == "__main__":
    unittest.main()
