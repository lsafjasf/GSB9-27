import random
import unittest

from eventstream import SSEParser, encode_event, heartbeat


SAMPLE = (
    b": stream-open\n"
    + heartbeat("ping")
    + encode_event("", event="empty", id="e-0")
    + b"id: e-1\r\nevent: multi\r\ndata: one\r\ndata: two\r\n\r\n"
    + b"data: no-id\n\n"
    + b"unknown-field: ignored\nid: e-2\ndata: tail"
)


def parse(chunks):
    parser = SSEParser()
    events = []
    for chunk in chunks:
        events.extend(parser.feed(chunk))
    events.extend(parser.close())
    return events


def chunks_by_size(raw, size):
    return [raw[index : index + size] for index in range(0, len(raw), size)]


class SplitEquivalenceTests(unittest.TestCase):
    def test_every_two_way_split_matches_whole_stream(self):
        expected = parse([SAMPLE])
        for cut in range(len(SAMPLE) + 1):
            with self.subTest(cut=cut):
                self.assertEqual(parse([SAMPLE[:cut], SAMPLE[cut:]]), expected)

    def test_every_fixed_chunk_size_matches_whole_stream(self):
        expected = parse([SAMPLE])
        for size in range(1, len(SAMPLE) + 1):
            with self.subTest(size=size):
                self.assertEqual(parse(chunks_by_size(SAMPLE, size)), expected)

    def test_random_chunkings_match_reference(self):
        rng = random.Random(338)
        parts = [heartbeat()]
        for index in range(150):
            if index % 7 == 0:
                parts.append(f": heartbeat-{index}\n\n".encode())
            parts.append(
                encode_event(
                    f"line {index}\n值 {index}",
                    event="fuzz" if index % 3 == 0 else None,
                    id=f"evt-{index}",
                    retry=index if index % 11 == 0 else None,
                )
            )
        raw = b"".join(parts)
        expected = parse([raw])
        for trial in range(250):
            chunks = []
            position = 0
            while position < len(raw):
                size = rng.randint(1, 257)
                chunks.append(raw[position : position + size])
                position += size
            with self.subTest(trial=trial):
                self.assertEqual(parse(chunks), expected)


if __name__ == "__main__":
    unittest.main()
