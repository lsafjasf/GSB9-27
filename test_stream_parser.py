"""StreamingParser 自测：分块等价性、边界情形、错误定位、内存上界。

运行：python3 -m unittest test_stream_parser -v
"""

import json
import random
import tracemalloc
import unittest

from stream_parser import (
    ParseError,
    RecordTooLargeError,
    StreamClosedError,
    StreamingParser,
    TruncatedInputError,
    parse_all,
)


def feed_in_chunks(data: bytes, sizes, max_record_size=1 << 20) -> list:
    """按给定块大小序列喂入，返回全部记录。"""
    parser = StreamingParser(max_record_size)
    out = []
    pos = 0
    for size in sizes:
        out.extend(parser.feed(data[pos:pos + size]))
        pos += size
    if pos < len(data):
        out.extend(parser.feed(data[pos:]))
    parser.finish()
    return out


def sample_data() -> bytes:
    lines = [
        {"id": i, "name": f"user-{i}", "tags": ["a", "b", i], "score": i * 1.5}
        for i in range(200)
    ]
    lines[57] = "plain string record"
    lines[120] = 42
    lines[180] = {"nested": {"中文": "多字节字符 ✓", "list": list(range(50))}}
    return "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines).encode("utf-8")


class EquivalenceTest(unittest.TestCase):
    """按任意分块边界喂入，结果必须与一次性解析逐条相同。"""

    def test_every_chunk_size(self):
        data = sample_data()
        expected = parse_all(data)
        for size in range(1, 257):
            got = feed_in_chunks(data, [size] * (len(data) // size + 1))
            self.assertEqual(expected, got, f"chunk_size={size}")

    def test_random_chunkings(self):
        data = sample_data()
        expected = parse_all(data)
        rng = random.Random(20260927)
        for trial in range(200):
            sizes = [rng.randrange(0, 4096) for _ in range(rng.randrange(1, 40))]
            got = feed_in_chunks(data, sizes)
            self.assertEqual(expected, got, f"trial={trial} sizes={sizes}")

    def test_empty_chunks_interleaved(self):
        data = sample_data()
        expected = parse_all(data)
        sizes = []
        for _ in range(500):
            sizes.extend([0, 0, 7])
        self.assertEqual(expected, feed_in_chunks(data, sizes))

    def test_empty_input(self):
        self.assertEqual([], parse_all(b""))
        parser = StreamingParser()
        self.assertEqual([], parser.feed(b""))
        parser.finish()

    def test_crlf_and_blank_lines(self):
        data = b'{"a": 1}\r\n\r\n{"b": 2}\n\n{"c": 3}\r\n'
        self.assertEqual([{"a": 1}, {"b": 2}, {"c": 3}], parse_all(data))


class BoundaryTest(unittest.TestCase):
    """记录跨块、超大记录、末尾不完整、提前结束。"""

    def test_record_spanning_many_chunks_byte_by_byte(self):
        record = {"payload": "x" * 10000, "n": 1}
        data = (json.dumps(record) + "\n").encode()
        expected = parse_all(data)
        got = feed_in_chunks(data, [1] * len(data))
        self.assertEqual(expected, got)

    def test_record_exceeds_buffer_limit(self):
        big = b'{"payload": "' + b"x" * 5000 + b'"}'
        data = b'{"ok": 1}\n' + big + b"\n"
        parser = StreamingParser(max_record_size=1024)
        self.assertEqual([{"ok": 1}], parser.feed(data[:10]))
        with self.assertRaises(RecordTooLargeError) as ctx:
            parser.feed(data[10:])
        err = ctx.exception
        self.assertEqual(1, err.record_index)      # 第 2 条记录（0 起）
        self.assertEqual(10, err.byte_offset)      # 该记录起始字节
        self.assertLessEqual(parser.buffered_bytes, 1024)  # 缓冲有界

    def test_oversized_record_arrives_in_one_chunk(self):
        data = b"x" * 2048 + b"\n"
        parser = StreamingParser(max_record_size=1024)
        with self.assertRaises(RecordTooLargeError):
            parser.feed(data)
        self.assertLessEqual(parser.buffered_bytes, 1024)

    def test_trailing_incomplete_record(self):
        data = b'{"a": 1}\n{"b": 2}\n{"c": 3'  # 末尾缺 } 且无换行
        parser = StreamingParser()
        out = parser.feed(data)
        self.assertEqual([{"a": 1}, {"b": 2}], out)  # 完整记录已产出
        with self.assertRaises(TruncatedInputError) as ctx:
            parser.finish()
        err = ctx.exception
        self.assertEqual(2, err.record_index)
        self.assertEqual(data.index(b'{"c"'), err.byte_offset)

    def test_abort_discards_residual(self):
        parser = StreamingParser()
        self.assertEqual([{"a": 1}], parser.feed(b'{"a": 1}\n{"partial": '))
        parser.abort()  # 上游中断：残留静默丢弃，不报错
        with self.assertRaises(StreamClosedError):
            parser.feed(b"more")

    def test_feed_after_finish_raises(self):
        parser = StreamingParser()
        parser.feed(b'{"a": 1}\n')
        parser.finish()
        with self.assertRaises(StreamClosedError):
            parser.feed(b'{"b": 2}\n')
        with self.assertRaises(StreamClosedError):
            parser.finish()


class ErrorLocationTest(unittest.TestCase):
    """错误必须报出第几条记录、第几行、出错字节是第几个字节。"""

    def test_json_error_position(self):
        good1 = b'{"a": 1}\n'
        good2 = b'{"b": 2}\n'
        bad = b'{"c": oops}\n'
        data = good1 + good2 + bad
        with self.assertRaises(ParseError) as ctx:
            parse_all(data)
        err = ctx.exception
        self.assertEqual(2, err.record_index)
        self.assertEqual(3, err.line_no)
        # 出错字节是 'oops' 的 'o'：记录起始 18 + 行内偏移 6 = 24
        self.assertEqual(len(good1) + len(good2) + 6, err.byte_offset)
        self.assertEqual(24, err.byte_offset)

    def test_error_byte_offset_is_exact_byte(self):
        # 出错字节本身：'✓' 不是合法 JSON 值；它前面有多字节字符，
        # 验证 byte_offset 按字节（而非字符）精确换算
        data = '{"ok": "中文"}\n{"bad": ✓}\n'.encode("utf-8")
        record_start = len('{"ok": "中文"}\n'.encode("utf-8"))  # 17
        with self.assertRaises(ParseError) as ctx:
            parse_all(data)
        err = ctx.exception
        self.assertEqual(1, err.record_index)
        self.assertEqual(2, err.line_no)
        self.assertEqual(record_start + len('{"bad": '.encode("utf-8")),
                         err.byte_offset)
        self.assertEqual(25, err.byte_offset)  # 精确值：17 + 8
        self.assertNotEqual(record_start, err.byte_offset)  # 不是记录起始字节

    def test_utf8_error_byte_offset_is_exact_byte(self):
        # 首个非法字节 \xff 在记录内下标 7 处
        data = b'{"a": 1}\n{"x": "\xff"}\n'
        with self.assertRaises(ParseError) as ctx:
            parse_all(data)
        err = ctx.exception
        self.assertEqual(1, err.record_index)
        self.assertEqual(2, err.line_no)
        self.assertEqual(9 + 7, err.byte_offset)

    def test_json_error_position_with_chunking(self):
        data = b'{"a": 1}\nBAD\n{"c": 3}\n'
        parser = StreamingParser()
        with self.assertRaises(ParseError) as ctx:
            for i in range(0, len(data), 3):
                parser.feed(data[i:i + 3])
        self.assertEqual(1, ctx.exception.record_index)
        self.assertEqual(2, ctx.exception.line_no)
        self.assertEqual(9, ctx.exception.byte_offset)

    def test_invalid_utf8_position(self):
        data = b'{"a": 1}\n\xff\xfe\n'
        with self.assertRaises(ParseError) as ctx:
            parse_all(data)
        self.assertEqual(1, ctx.exception.record_index)
        self.assertEqual(2, ctx.exception.line_no)
        self.assertEqual(9, ctx.exception.byte_offset)


class MemoryBoundTest(unittest.TestCase):
    """流式解析的内存峰值必须有界，且与输入总量无关。"""

    def test_streaming_peak_is_bounded(self):
        record = json.dumps({"id": 0, "payload": "y" * 200}).encode() + b"\n"
        data = record * 40000  # ~ 9 MB
        chunk_size = 8192

        tracemalloc.start()
        parser = StreamingParser(max_record_size=64 << 10)
        count = 0
        for i in range(0, len(data), chunk_size):
            count += len(parser.feed(data[i:i + chunk_size]))  # 下游直接消费，不累积
        parser.finish()
        _, stream_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(40000, count)

        tracemalloc.start()
        all_records = parse_all(data)  # 一次性解析：结果全量驻留
        _, oneshot_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(40000, len(all_records))

        print(f"\n[memory] input={len(data)/1e6:.1f} MB, "
              f"streaming peak={stream_peak/1e6:.2f} MB, "
              f"one-shot peak={oneshot_peak/1e6:.2f} MB")
        # 流式峰值远小于输入规模（9 MB 输入，峰值应 < 1 MB）
        self.assertLess(stream_peak, 1 << 20)
        # 且远小于一次性解析（结果列表全量驻留内存）
        self.assertLess(stream_peak, oneshot_peak // 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
