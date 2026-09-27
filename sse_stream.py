"""SSE 风格事件流解析库（仅标准库）。

特性：
- 增量解析：任意字节切分喂入，产出事件序列完全一致。
- 字段行 / 数据行 / 结束标记（空行）解析，支持多行 data、注释行（心跳）。
- 心跳（以 ':' 开头的注释行）不产生业务事件。
- 记录 last_event_id，供断线续传时携带 Last-Event-ID。
- ResumableEventStream：断线自动重连 + 按事件 id 去重，实现 exactly-once 语义。

行尾兼容 \\n、\\r\\n、\\r；UTF-8 多字节字符跨 chunk 切分安全。
"""

from __future__ import annotations

import codecs
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, List, Optional


@dataclass(frozen=True)
class Event:
    """一条业务事件。id 可能为 None（服务端未给 id 的事件无法续传去重）。"""

    data: str
    event: str = "message"
    id: Optional[str] = None


class SSEParser:
    """增量事件流解析器。

    用法：
        parser = SSEParser()
        events = parser.feed(chunk_bytes)   # 任意切分
        ...
        events = parser.close()             # 连接关闭时冲刷

    解析规则（对齐 W3C eventsource 规范）：
    - 空行 = 事件结束标记，派发事件。
    - 以 ':' 开头的行 = 注释 / 心跳，忽略。
    - 'field: value'（冒号后最多去掉一个空格）；无冒号的行视为字段名、值为空串。
    - 多条 data 行以 '\\n' 拼接；event 缺省为 'message'。
    - 无 data 的事件默认不派发（但其中的 id 仍会更新 last_event_id）；
      传 dispatch_empty=True 可改为派发空数据事件。
    - 未知字段忽略；retry 字段记录在 parser.retry（毫秒）。
    """

    def __init__(self, dispatch_empty: bool = False) -> None:
        self.dispatch_empty = dispatch_empty
        self.last_event_id: Optional[str] = None
        self.retry: Optional[int] = None
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buf = ""
        self._data: List[str] = []
        self._event_type = ""
        self._pending_id: Optional[str] = None
        self._closed = False

    def feed(self, chunk: bytes) -> List[Event]:
        """喂入任意大小的字节块，返回本次产出的完整事件。"""
        if self._closed:
            raise RuntimeError("parser already closed")
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("feed() expects bytes")
        self._buf += self._decoder.decode(bytes(chunk))
        return self._drain()

    def close(self) -> List[Event]:
        """连接关闭：冲刷解码器与行缓冲。

        注意：末尾未以空行结束的事件（含不足一行的残片）不完整，按规范丢弃。
        """
        if self._closed:
            return []
        self._closed = True
        self._buf += self._decoder.decode(b"", final=True)
        events = self._drain()
        # 末尾不足一行的残片连同未完结的事件一起丢弃（对齐 eventsource 规范）
        self._buf = ""
        self._data = []
        self._event_type = ""
        self._pending_id = None
        return events

    # ---- 内部实现 ----

    def _drain(self) -> List[Event]:
        events: List[Event] = []
        while True:
            line, rest = self._split_line(self._buf)
            if line is None:
                break
            self._buf = rest
            ev = self._process_line(line)
            if ev is not None:
                events.append(ev)
        return events

    @staticmethod
    def _split_line(buf: str):
        """切出一行。返回 (line, rest)；数据不足一行时返回 (None, buf)。

        支持 \\n、\\r\\n、\\r 三种行尾；buffer 末尾的孤立 \\r 等待下一 chunk。
        """
        for i, ch in enumerate(buf):
            if ch == "\n":
                return buf[:i], buf[i + 1 :]
            if ch == "\r":
                if i + 1 < len(buf):
                    if buf[i + 1] == "\n":
                        return buf[:i], buf[i + 2 :]
                    return buf[:i], buf[i + 1 :]
                return None, buf  # \r 在末尾，等下一个字节判断是否为 \r\n
        return None, buf

    def _process_line(self, line: str) -> Optional[Event]:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None  # 注释 / 心跳
        field_name, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if field_name == "data":
            self._data.append(value)
        elif field_name == "event":
            self._event_type = value
        elif field_name == "id":
            if "\x00" not in value:  # 含 NUL 的 id 按规范忽略
                self._pending_id = value
        elif field_name == "retry":
            if value.isdigit():
                self.retry = int(value)
        # 未知字段忽略
        return None

    def _dispatch(self) -> Optional[Event]:
        has_data = bool(self._data)
        data = "\n".join(self._data)
        self._data = []
        event_type = self._event_type or "message"
        self._event_type = ""
        if self._pending_id is not None:
            self.last_event_id = self._pending_id
            self._pending_id = None
        if not has_data and not self.dispatch_empty:
            return None  # 无数据的事件（含纯心跳空行）不派发，但 id 已更新
        return Event(data=data, event=event_type, id=self.last_event_id)


class IdDeduper:
    """按事件 id 去重（有界 LRU，内存 O(capacity)）。

    重连后服务端可能从 last_event_id 起（含）重放，重复 id 的事件只放行一次。
    无 id 的事件无法去重，直接放行。
    """

    def __init__(self, capacity: int = 4096) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._seen: OrderedDict[str, None] = OrderedDict()

    def is_duplicate(self, event_id: Optional[str]) -> bool:
        if event_id is None:
            return False
        if event_id in self._seen:
            self._seen.move_to_end(event_id)
            return True
        self._seen[event_id] = None
        if len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return False


class GiveUp(Exception):
    """连接器抛出此异常时，ResumableEventStream 停止重连并结束迭代。"""


@dataclass
class ResumableEventStream:
    """断线续传事件流客户端。

    connect(last_event_id) -> Iterable[bytes]：
        建立连接的工厂，必须把 last_event_id 作为 Last-Event-ID 带给服务端，
        返回一个字节 chunk 的可迭代对象；迭代结束或抛异常都视为断线。
    断线后用解析器记录的 last_event_id 重连，重复 id 的事件由 IdDeduper 去重，
    对上层呈现 exactly-once 的事件序列。
    """

    connect: Callable[[Optional[str]], Iterable[bytes]]
    dispatch_empty: bool = False
    dedup_capacity: int = 4096
    max_reconnects: Optional[int] = None  # None = 无限重连

    def events(self) -> Iterator[Event]:
        last_id: Optional[str] = None
        deduper = IdDeduper(self.dedup_capacity)
        reconnects = 0
        while True:
            parser = SSEParser(dispatch_empty=self.dispatch_empty)
            try:
                stream = self.connect(last_id)
                for chunk in stream:
                    for ev in parser.feed(chunk):
                        if not deduper.is_duplicate(ev.id):
                            yield ev
                for ev in parser.close():
                    if not deduper.is_duplicate(ev.id):
                        yield ev
            except GiveUp:
                return
            except Exception:
                pass  # 网络错误：按断线处理，重连
            # 连接结束（含服务端提前关闭）：用最新 last_event_id 重连
            last_id = parser.last_event_id if parser.last_event_id is not None else last_id
            reconnects += 1
            if self.max_reconnects is not None and reconnects > self.max_reconnects:
                return
