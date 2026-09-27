import unittest

from eventstream import Event, SSEParser, encode_event, heartbeat


def parse_chunks(chunks):
    parser = SSEParser()
    events = []
    for chunk in chunks:
        events.extend(parser.feed(chunk))
    events.extend(parser.close())
    return events


class SSEParserTests(unittest.TestCase):
    def test_fields_multiline_data_comments_and_heartbeat(self):
        raw = (
            b": heartbeat\n"
            b"\n"
            b"retry: 250\n"
            b"id: evt-1\n"
            b"event: greeting\n"
            b"data: hello\n"
            b"data:world\n"
            b"data:  leading-space-kept\n"
            b"unknown: ignored\n"
            b"\n"
            + heartbeat()
        )

        events = parse_chunks([raw])

        self.assertEqual(
            events,
            [
                Event(
                    data="hello\nworld\n leading-space-kept",
                    event="greeting",
                    id="evt-1",
                    retry=250,
                )
            ],
        )

    def test_empty_event_and_empty_lines_in_data(self):
        events = parse_chunks([b"event: empty\ndata:\n\n", b"data: a\ndata:\ndata: b\n\n"])

        self.assertEqual(events[0], Event(data="", event="empty"))
        self.assertEqual(events[1], Event(data="a\n\nb"))

    def test_missing_fields_and_id_only_block(self):
        events = parse_chunks(
            [
                b"data: no metadata\n\n",
                b"id: cursor-7\n\n",
                b"data: inherits parser id\n\n",
            ]
        )

        self.assertEqual(events[0], Event(data="no metadata"))
        self.assertEqual(events[1], Event(data="inherits parser id", id="cursor-7"))

    def test_comment_only_and_id_only_blocks_do_not_dispatch(self):
        events = parse_chunks([b": ping\n\nid: 9\n\nretry: 1000\n\n"])

        self.assertEqual(events, [])

    def test_crlf_cr_and_lf_line_endings(self):
        events = parse_chunks([b"id: 1\r\ndata: a\r\n\r\nid: 2\rdata: b\r\r"])

        self.assertEqual(
            events,
            [Event(data="a", id="1"), Event(data="b", id="2")],
        )

    def test_carriage_return_at_eof_terminates_line(self):
        events = parse_chunks([b"data: final\r"])

        self.assertEqual(events, [Event(data="final")])

    def test_long_data_line_across_chunks(self):
        data = "x" * 1_000_000
        raw = encode_event(data, id="long")
        chunks = [raw[index : index + 8191] for index in range(0, len(raw), 8191)]

        events = parse_chunks(chunks)

        self.assertEqual(events, [Event(data=data, id="long")])

    def test_unicode_and_invalid_utf8_are_deterministic(self):
        valid = parse_chunks(["data: 你好 🌍\n\n".encode()])
        invalid = parse_chunks([b"data: \xff\xfe\n\n"])

        self.assertEqual(valid[0].data, "你好 🌍")
        self.assertEqual(invalid[0].data, "��")

    def test_early_close_terminates_pending_line_and_event(self):
        parser = SSEParser()
        completed = parser.feed(b"id: 1\ndata: complete\n\nid: 2\ndata: partial")
        closed = parser.close()

        self.assertEqual(completed, [Event(data="complete", id="1")])
        self.assertEqual(closed, [Event(data="partial", id="2")])

    def test_early_close_without_data_does_not_dispatch(self):
        parser = SSEParser()
        self.assertEqual(parser.feed(b": heartbeat\nid: 10"), [])
        self.assertEqual(parser.close(), [])

    def test_buffer_limit_rejects_unbounded_incomplete_line(self):
        parser = SSEParser(max_buffer_size=8)

        with self.assertRaises(BufferError):
            parser.feed(b"data: too-long")

    def test_encoder_round_trip_multiline(self):
        raw = encode_event("a\nb\n", event="multi", id="m-1", retry=10)

        self.assertEqual(
            parse_chunks([raw]),
            [Event(data="a\nb\n", event="multi", id="m-1", retry=10)],
        )


if __name__ == "__main__":
    unittest.main()
