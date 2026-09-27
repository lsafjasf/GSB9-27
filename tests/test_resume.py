from contextlib import contextmanager
import unittest

from eventstream import EventLog, EventStreamClient, UnknownLastEventId, encode_event


class MemoryTransport:
    def __init__(self, payload, *, chunk_size=13, fail=False):
        self.payload = payload
        self.chunk_size = chunk_size
        self.fail = fail
        self.headers = []
        self.offset = 0

    @contextmanager
    def open(self, headers):
        self.headers.append(dict(headers))
        self.offset = 0
        try:
            yield self
        finally:
            pass

    def read(self, size=-1):
        if self.offset >= len(self.payload):
            if self.fail:
                self.fail = False
                raise ConnectionError("simulated network cut")
            return b""
        amount = self.chunk_size if size is None or size < 0 else min(size, self.chunk_size)
        chunk = self.payload[self.offset : self.offset + amount]
        self.offset += len(chunk)
        return chunk


class ResumeTests(unittest.TestCase):
    def make_log(self, count=5):
        log = EventLog()
        for index in range(1, count + 1):
            log.append(f"payload-{index}", id=f"evt-{index}")
        return log

    def test_reconnect_sends_last_event_id_and_continues(self):
        log = self.make_log()
        first_two = b"".join(stored.raw for stored in log.read_after(None)[:2])
        first = MemoryTransport(first_two, chunk_size=7, fail=True)
        second = MemoryTransport(log.encode_after("evt-2"), chunk_size=5)
        client = EventStreamClient(chunk_size=11)
        received = []

        with self.assertRaises(ConnectionError):
            client.consume(first, received.append)
        self.assertEqual(client.last_event_id, "evt-2")

        client.consume(second, received.append)

        self.assertEqual(second.headers, [{"Last-Event-ID": "evt-2"}])
        self.assertEqual([event.id for event in received], [f"evt-{i}" for i in range(1, 6)])
        self.assertEqual([event.data for event in received], [f"payload-{i}" for i in range(1, 6)])

    def test_duplicate_replayed_events_are_suppressed(self):
        payload = b"".join(
            [
                encode_event("one", id="evt-1"),
                encode_event("two", id="evt-2"),
                encode_event("two replay", id="evt-2"),
                encode_event("one replay", id="evt-1"),
                encode_event("three", id="evt-3"),
            ]
        )
        client = EventStreamClient()
        received = []

        client.consume(MemoryTransport(payload, chunk_size=3), received.append)

        self.assertEqual([event.data for event in received], ["one", "two", "three"])
        self.assertEqual(client.last_event_id, "evt-3")

    def test_bounded_dedup_cache_evicts_oldest_id(self):
        payload = b"".join(
            encode_event(f"value-{index}", id=f"evt-{index}") for index in [1, 2, 3, 1]
        )
        client = EventStreamClient(dedup_size=2)
        received = []

        client.consume(MemoryTransport(payload), received.append)

        self.assertEqual([event.data for event in received], ["value-1", "value-2", "value-3", "value-1"])

    def test_empty_id_is_delivered_but_not_used_as_resume_token(self):
        payload = encode_event("empty id", id="") + encode_event("next", id="evt-1")
        client = EventStreamClient()
        received = []

        client.consume(MemoryTransport(payload), received.append)

        self.assertEqual([event.id for event in received], ["", "evt-1"])
        self.assertEqual(client.request_headers(), {"Last-Event-ID": "evt-1"})

    def test_server_rejects_unknown_or_expired_resume_token(self):
        log = self.make_log(2)

        with self.assertRaises(UnknownLastEventId):
            log.read_after("missing")

    def test_clean_early_close_allows_next_connection_to_resume(self):
        log = self.make_log(4)
        first_two = b"".join(stored.raw for stored in log.read_after(None)[:2])
        first = MemoryTransport(first_two, chunk_size=1024)
        second = MemoryTransport(log.encode_after("evt-2"))
        client = EventStreamClient()
        received = []

        client.consume(first, received.append)
        client.consume(second, received.append)

        self.assertEqual(second.headers, [{"Last-Event-ID": "evt-2"}])
        self.assertEqual([event.id for event in received], ["evt-1", "evt-2", "evt-3", "evt-4"])

    def test_initial_request_has_no_resume_header(self):
        transport = MemoryTransport(b"")
        EventStreamClient().consume(transport, lambda event: None)

        self.assertEqual(transport.headers, [{}])


if __name__ == "__main__":
    unittest.main()
