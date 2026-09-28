"""流式 NDJSON 解析器（仅标准库）。

记录格式：NDJSON —— 每条记录是一行 UTF-8 JSON 值，以 ``\\n`` 分隔
（兼容 ``\\r\\n``）。空行（去掉 ``\\r`` 后长度为 0）被跳过，不计入记录序号。

缓冲语义
--------
- 内部缓冲 ``_buf`` 只保存“尚未看到换行符的不完整记录”，
  其长度严格不超过 ``max_record_size``（默认 1 MiB）。
- 单条记录（不含换行符）一旦超过 ``max_record_size``，立即抛出
  :class:`RecordTooLargeError`，缓冲拒绝继续增长 —— 这是唯一的有界策略，
  不会为了容纳超大记录而扩容。
- 残留数据的处置：
  * ``finish()`` 时缓冲非空 -> 抛出 :class:`TruncatedInputError`（报错，不丢弃）；
  * ``abort()``（上游中断）-> 残留数据直接丢弃，不报错；
  * 任何解析错误抛出后，解析器状态不再可靠，调用方应丢弃实例。

错误定位
--------
所有 :class:`ParserError` 子类都携带：
- ``record_index``：出错记录是第几条（0 起，只数成功产出的记录之前的序号，
  即“正在解析的第 N 条”，N 从 0 开始）；
- ``line_no``：出错记录所在的行号（1 起，空行也计数）；
- ``byte_offset``：出错字节本身在整个输入流中的绝对偏移（0 起）。
  JSON 错误按 ``JSONDecodeError.pos`` 把字符位置换算成 UTF-8 字节偏移，
  编码错误按 ``UnicodeDecodeError.start`` 定位到首个非法字节；
  记录级错误（超限 / 截断）定位到该记录的首字节。
"""

from __future__ import annotations

import json

DEFAULT_MAX_RECORD_SIZE = 1 << 20  # 1 MiB

_SKIP = object()


class ParserError(Exception):
    """解析错误基类，携带 record_index、line_no 与 byte_offset。"""

    def __init__(self, message: str, record_index: int, line_no: int, byte_offset: int):
        super().__init__(message)
        self.record_index = record_index
        self.line_no = line_no
        self.byte_offset = byte_offset


class ParseError(ParserError):
    """记录内容非法（UTF-8 解码失败或 JSON 解析失败）。"""


class RecordTooLargeError(ParserError):
    """单条记录超过 max_record_size，缓冲拒绝继续增长。"""


class TruncatedInputError(ParserError):
    """finish() 时缓冲中仍残留不完整的记录。"""


class StreamClosedError(Exception):
    """finish()/abort() 之后又调用了 feed()/finish()。"""


class StreamingParser:
    """增量式 NDJSON 解析器。

    用法::

        parser = StreamingParser(max_record_size=1 << 20)
        for chunk in source:
            for record in parser.feed(chunk):
                handle(record)
        parser.finish()          # 或上游中断时 parser.abort()
    """

    def __init__(self, max_record_size: int = DEFAULT_MAX_RECORD_SIZE):
        if max_record_size <= 0:
            raise ValueError("max_record_size must be positive")
        self._max = max_record_size
        self._buf = bytearray()
        self._consumed = 0   # 已完整消费的字节数 == 缓冲起始的绝对偏移
        self._records = 0    # 已成功产出的记录数 == 下一条记录的序号
        self._lines = 0      # 已完整消费的行数（含空行）== 当前行号 - 1
        self._closed = False

    # -- 只读状态 -----------------------------------------------------

    @property
    def records_emitted(self) -> int:
        return self._records

    @property
    def bytes_consumed(self) -> int:
        return self._consumed

    @property
    def buffered_bytes(self) -> int:
        return len(self._buf)

    # -- 核心接口 -----------------------------------------------------

    def feed(self, chunk) -> list:
        """喂入一块字节数据，返回本次完整解析出的记录列表。

        空块（b""）是合法 no-op。内部缓冲长度始终 <= max_record_size。
        """
        if self._closed:
            raise StreamClosedError("feed() after finish()/abort()")
        if not chunk:
            return []
        if not isinstance(chunk, (bytes, bytearray)):
            chunk = bytes(chunk)

        out = []
        pos = 0
        end = len(chunk)
        while pos < end:
            nl = chunk.find(b"\n", pos)
            if nl < 0:
                # 整块都是不完整记录的尾部：只有放得下才进缓冲
                if len(self._buf) + (end - pos) > self._max:
                    raise RecordTooLargeError(
                        f"record #{self._records} (line {self._lines + 1}) "
                        f"exceeds max_record_size={self._max} "
                        f"(record starts at byte {self._consumed})",
                        self._records, self._lines + 1, self._consumed,
                    )
                self._buf += chunk[pos:]
                pos = end
            else:
                seg = chunk[pos:nl]  # 不含换行符
                if self._buf:
                    line = bytes(self._buf) + seg
                    self._buf.clear()
                else:
                    line = seg
                if len(line) > self._max:
                    raise RecordTooLargeError(
                        f"record #{self._records} (line {self._lines + 1}) "
                        f"exceeds max_record_size={self._max} "
                        f"(record starts at byte {self._consumed})",
                        self._records, self._lines + 1, self._consumed,
                    )
                start = self._consumed
                line_no = self._lines + 1
                self._consumed += len(line) + 1
                self._lines += 1
                record = self._parse_line(line, start, line_no)
                if record is not _SKIP:
                    out.append(record)
                    self._records += 1
                pos = nl + 1
        return out

    def finish(self) -> None:
        """正常结束。缓冲中有残留的不完整记录时报错（不静默丢弃）。"""
        if self._closed:
            raise StreamClosedError("finish() called twice")
        self._closed = True
        if self._buf:
            leftover = len(self._buf)
            raise TruncatedInputError(
                f"truncated record #{self._records} (line {self._lines + 1}): "
                f"{leftover} leftover byte(s) at byte {self._consumed} "
                f"(input ended mid-record)",
                self._records, self._lines + 1, self._consumed,
            )

    def abort(self) -> None:
        """上游中断时提前结束：残留的不完整记录直接丢弃，不报错。"""
        self._closed = True
        self._buf.clear()

    # -- 内部 ---------------------------------------------------------

    def _parse_line(self, line: bytes, start: int, line_no: int):
        if line.endswith(b"\r"):
            line = line[:-1]
        if not line:
            return _SKIP
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            # exc.start 是行内首个非法字节的下标 -> 换算成流内绝对字节偏移
            err_off = start + exc.start
            raise ParseError(
                f"record #{self._records} (line {line_no}): invalid UTF-8 "
                f"at byte {err_off}: {exc}",
                self._records, line_no, err_off,
            ) from exc
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            # exc.pos 是解码后文本中的字符下标，需按 UTF-8 重新编码换算成
            # 行内字节偏移，再加上记录起始偏移得到出错字节的绝对位置
            err_off = start + len(text[:exc.pos].encode("utf-8"))
            raise ParseError(
                f"record #{self._records} (line {line_no}): invalid JSON "
                f"at byte {err_off} (record line {exc.lineno}, "
                f"col {exc.colno}): {exc.msg}",
                self._records, line_no, err_off,
            ) from exc


def parse_all(data: bytes, max_record_size: int = DEFAULT_MAX_RECORD_SIZE) -> list:
    """一次性解析（参考实现）：与任意分块喂入的结果逐条相同。"""
    parser = StreamingParser(max_record_size)
    out = parser.feed(data)
    parser.finish()
    return out
