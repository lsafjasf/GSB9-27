"""sse_stream 自测：解析正确性、切分等价性对拍、断线续传与去重、边界情形。

运行：python3 test_sse_stream.py -v
"""

import random
import unittest

from sse_stream import Event, GiveUp, IdDeduper, ResumableEventStream, SSEParser


def parse_all(stream: bytes, chunk_size: int, **kw):
    """以固定 chunk_size 喂入整段流，返回事件序列。"""
    p = SSEParser(**kw)
    events = []
    for i in range(0, len(stream), chunk_size):
        events.extend(p.feed(stream[i : i + chunk_size]))
    events.extend(p.close())
    return events


def parse_random_split(stream: bytes, rng: random.Random, **kw):
    """随机切分喂入。"""
    p = SSEParser(**kw)
    events = []
    i = 0
    while i < len(stream):
        n = rng.randint(1, max(1, len(stream) // 3))
        events.extend(p.feed(stream[i : i + n]))
        i += n
    events.extend(p.close())
    return events


# 覆盖各种特性的样例流：多行 data、event 字段、注释/心跳、CRLF/CR/LF 混合行尾、
# 无冒号行、冒号后无空格、多字节 UTF-8（含 emoji）、空 data、未知字段、retry。
SAMPLE_STREAM = (
    b": heartbeat\n"
    b"\n"  # 纯心跳空行：不产生事件
    b"id: 1\nevent: greeting\ndata: hello\ndata: world\n\n"
    b"data:no-space-after-colon\r\n"
    b"data\r\n"  # 无冒号 => 字段 data，值为空串
    b"\r\n"
    b"id:2\rdata: cr-line-ending\r\r"
    b"data: \xe4\xb8\xad\xe6\x96\x87\xe6\x95\xb0\xe6\x8d\xae \xf0\x9f\x9a\x80\n"
    b"data: \xe6\x8b\xbc\xe6\x8e\xa5\n\n"
    b"unknown-field: ignored\nretry: 3000\ndata: after unknown\n\n"
    b":another comment\n"
    b"event: typed\ndata: x\n\n"
    b"id: 99\n\n"  # 只有 id、无 data：不派发，但更新 last_event_id
    b"data: final\n\n"
)

EXPECTED_EVENTS = [
    Event(data="hello\nworld", event="greeting", id="1"),
    Event(data="no-space-after-colon\n", event="message", id="1"),
    Event(data="cr-line-ending", event="message", id="2"),
    Event(data="中文数据 🚀\n拼接", event="message", id="2"),
    Event(data="after unknown", event="message", id="2"),
    Event(data="x", event="typed", id="2"),
    Event(data="final", event="message", id="99"),
]


class TestParsing(unittest.TestCase):
    def test_basic_fields_and_multiline_data(self):
        events = parse_all(b"id: 7\nevent: e\ndata: a\ndata: b\ndata: c\n\n", 1024)
        self.assertEqual(events, [Event(data="a\nb\nc", event="e", id="7")])

    def test_heartbeat_comment_not_dispatched(self):
        events = parse_all(b": ping\n: pong\n\n: still nothing\n", 1)
        self.assertEqual(events, [])

    def test_default_event_type_and_missing_id(self):
        events = parse_all(b"data: hello\n\n", 1024)
        self.assertEqual(events, [Event(data="hello", event="message", id=None)])

    def test_line_without_colon_is_field_with_empty_value(self):
        events = parse_all(b"data\n\n", 1024)
        self.assertEqual(events, [Event(data="", event="message", id=None)])

    def test_id_only_block_updates_last_event_id_without_event(self):
        p = SSEParser()
        self.assertEqual(p.feed(b"id: 42\n\n"), [])
        self.assertEqual(p.last_event_id, "42")
        self.assertEqual(p.feed(b"data: x\n\n"), [Event(data="x", id="42")])

    def test_empty_event_not_dispatched_by_default(self):
        self.assertEqual(parse_all(b"event: ping\n\n", 1024), [])

    def test_empty_event_dispatched_when_enabled(self):
        events = parse_all(b"event: ping\n\n", 1024, dispatch_empty=True)
        self.assertEqual(events, [Event(data="", event="ping", id=None)])

    def test_id_with_nul_ignored(self):
        p = SSEParser()
        p.feed(b"id: bad\x00id\ndata: x\n\n")
        self.assertIsNone(p.last_event_id)

    def test_retry_field_recorded(self):
        p = SSEParser()
        p.feed(b"retry: 2500\ndata: x\n\n")
        self.assertEqual(p.retry, 2500)

    def test_very_long_data_line(self):
        payload = "x" * (2 * 1024 * 1024)  # 2MB 单行
        events = parse_all(("data: " + payload + "\n\n").encode(), 65536)
        self.assertEqual(events, [Event(data=payload)])

    def test_server_early_close_discards_incomplete_event(self):
        p = SSEParser()
        self.assertEqual(p.feed(b"data: complete\n\n"), [Event(data="complete")])
        p.feed(b"data: truncated")  # 服务端提前关闭，事件未完结
        self.assertEqual(p.close(), [])  # 不完整事件被丢弃，不派发

    def test_close_discards_trailing_line_without_newline(self):
        p = SSEParser()
        self.assertEqual(p.feed(b"data: a\n\n"), [Event(data="a")])
        p.feed(b"id: 9")  # 最后一行没有行尾符就断开了
        self.assertEqual(p.close(), [])
        self.assertIsNone(p.last_event_id)  # 残片整体丢弃，id 不生效

    def test_feed_after_close_raises(self):
        p = SSEParser()
        p.close()
        with self.assertRaises(RuntimeError):
            p.feed(b"data: x\n\n")

    def test_invalid_utf8_replaced_not_crash(self):
        events = parse_all(b"data: \xff\xfe broken\n\n", 1)
        self.assertEqual(len(events), 1)
        self.assertIn("broken", events[0].data)


class TestSplitEquivalence(unittest.TestCase):
    """对拍：同一段流，任意切分方式喂入，事件序列必须完全一致。"""

    def test_all_fixed_chunk_sizes(self):
        baseline = parse_all(SAMPLE_STREAM, len(SAMPLE_STREAM))  # 一次喂完
        self.assertEqual(baseline, EXPECTED_EVENTS)
        for size in range(1, 257):
            with self.subTest(chunk_size=size):
                self.assertEqual(parse_all(SAMPLE_STREAM, size), baseline)

    def test_random_splits(self):
        baseline = parse_all(SAMPLE_STREAM, len(SAMPLE_STREAM))
        rng = random.Random(20260927)
        for trial in range(300):
            with self.subTest(trial=trial):
                self.assertEqual(parse_random_split(SAMPLE_STREAM, rng), baseline)

    def test_random_binary_stream_fuzz(self):
        """随机生成的合法流，逐字节 vs 随机切分对拍。"""
        rng = random.Random(7)
        for trial in range(50):
            parts = []
            for _ in range(rng.randint(1, 30)):
                kind = rng.randrange(6)
                if kind == 0:
                    parts.append(f"data: {rng.randrange(10**6)} 数据🚀\n".encode())
                elif kind == 1:
                    parts.append(f"id: {rng.randrange(100)}\n".encode())
                elif kind == 2:
                    parts.append(b": heartbeat\n")
                elif kind == 3:
                    parts.append(f"event: t{rng.randrange(5)}\n".encode())
                elif kind == 4:
                    parts.append(rng.choice([b"\n", b"\r\n", b"\r"]))
                else:
                    parts.append(f"data:multi\n".encode())
            stream = b"".join(parts)
            expected = parse_all(stream, 1)
            self.assertEqual(parse_random_split(stream, rng), expected)


class TestDeduper(unittest.TestCase):
    def test_duplicate_ids_filtered(self):
        d = IdDeduper(capacity=4)
        self.assertFalse(d.is_duplicate("a"))
        self.assertTrue(d.is_duplicate("a"))
        self.assertFalse(d.is_duplicate("b"))
        self.assertFalse(d.is_duplicate(None))  # 无 id 不去重，放行
        self.assertFalse(d.is_duplicate(None))

    def test_capacity_bounded(self):
        d = IdDeduper(capacity=2)
        d.is_duplicate("a")
        d.is_duplicate("b")
        d.is_duplicate("c")  # 挤出最旧的 a
        self.assertFalse(d.is_duplicate("a"))  # a 已被淘汰，视为新事件
        self.assertTrue(d.is_duplicate("c"))


class FakeServer:
    """假服务端：持有完整事件序列，按 Last-Event-ID 续传（含起点，模拟重放），
    并在指定位置切断连接（提前关闭）。"""

    def __init__(self, events, cut_points):
        self.events = events  # list of (id, data)
        self.cut_points = list(cut_points)  # 每次连接在发送 N 条后切断；None 表示发完
        self.requests = []  # 记录每次连接携带的 Last-Event-ID

    def connect(self, last_event_id):
        self.requests.append(last_event_id)
        start = 0
        if last_event_id is not None:
            ids = [e[0] for e in self.events]
            start = ids.index(last_event_id)  # 含起点重放 => 客户端必须去重
        cut = self.cut_points.pop(0) if self.cut_points else None
        sent = 0
        for eid, data in self.events[start:]:
            if cut is not None and sent >= cut:
                return  # 服务端提前关闭
            sent += 1
            yield f"id: {eid}\ndata: {data}\n\n".encode()


class TestResume(unittest.TestCase):
    def test_resume_with_dedup_exactly_once(self):
        all_events = [(str(i), f"payload-{i}") for i in range(20)]
        # 第一次连接发 7 条后断，第二次发 5 条后断，之后发完
        server = FakeServer(all_events, cut_points=[7, 5, None])
        client = ResumableEventStream(server.connect, max_reconnects=10)
        got = list(client.events())

        # exactly-once：20 条，无重复、无丢失、顺序正确
        self.assertEqual([e.id for e in got], [str(i) for i in range(20)])
        self.assertEqual([e.data for e in got], [f"payload-{i}" for i in range(20)])
        # 重连时携带了上次的 Last-Event-ID
        self.assertEqual(server.requests[0], None)
        self.assertEqual(server.requests[1], "6")
        self.assertEqual(server.requests[2], "10")  # 重放含起点，第二次连接最后收到 id=10

    def test_resume_random_cuts_and_splits(self):
        """随机断点 + 随机字节切分，序列仍 exactly-once。"""
        rng = random.Random(99)
        all_events = [(str(i), f"d{i}") for i in range(200)]
        cuts = [rng.randint(0, 30) for _ in range(40)] + [None]

        server = FakeServer(all_events, cut_points=cuts)

        def connect(last_id):
            # 把服务端输出再随机切碎，模拟网络层任意切分
            chunks = []
            for piece in server.connect(last_id):
                data = piece
                while data:
                    n = rng.randint(1, max(1, len(data)))
                    chunks.append(data[:n])
                    data = data[n:]
            return chunks

        client = ResumableEventStream(connect, max_reconnects=100)
        got = list(client.events())
        self.assertEqual([e.id for e in got], [str(i) for i in range(200)])

    def test_give_up_stops_iteration(self):
        def connect(last_id):
            raise GiveUp()

        client = ResumableEventStream(connect, max_reconnects=3)
        self.assertEqual(list(client.events()), [])

    def test_max_reconnects_bounds_retries(self):
        calls = []

        def connect(last_id):
            calls.append(last_id)
            return iter([b"data: x\n\n"])  # 每次发一条就"提前关闭"

        client = ResumableEventStream(connect, max_reconnects=3)
        got = list(client.events())
        self.assertEqual(len(got), 4)  # 首次 + 3 次重连
        self.assertEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main()
