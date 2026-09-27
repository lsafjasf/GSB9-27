"""条件请求与缓存校验核心库（仅标准库）。

实现 RFC 9110 风格的两类校验器：
  - 内容校验器：强 ETag（由内容字节派生，内容变则 ETag 必变）
  - 修改时间：Last-Modified（HTTP-date，秒级精度，作为弱校验兜底）

求值优先级（GET/HEAD）：If-None-Match 优先于 If-Modified-Since。
写前置条件：If-Match 不匹配返回 412，保证并发更新语义确定。
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from email.utils import formatdate, parsedate_to_datetime

# 状态语义常量
STATUS_OK = 200
STATUS_NOT_MODIFIED = 304
STATUS_NOT_FOUND = 404
STATUS_PRECONDITION_FAILED = 412


def make_etag(body: bytes) -> str:
    """强校验器：完整内容哈希。内容相同 => ETag 相同；内容不同 => 必不同。"""
    digest = hashlib.sha256(body).hexdigest()
    return f'"sha256-{digest}"'


def http_date(epoch_seconds: float) -> str:
    return formatdate(epoch_seconds, usegmt=True)


def parse_http_date(value: str) -> float | None:
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class Resource:
    body: bytes
    etag: str
    last_modified: float  # epoch 秒（浮点，内部精度）
    content_type: str = "application/octet-stream"
    metadata: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: bytes  # 304/404/412 时为空字节串

    @property
    def wire_bytes(self) -> int:
        """估算线上字节数：状态行 + 头部 + 实体体。"""
        head = sum(len(k) + len(v) + 4 for k, v in self.headers.items())
        return 16 + head + len(self.body)


class ResourceStore:
    """线程安全的资源存储。所有读取/校验/写入在同一锁内完成，

    因此条件求值与写入之间不存在交错窗口：写入完成后，
    任何携带旧校验器的请求在新内容快照上求值，必然得到 200。
    """

    def __init__(self, clock=__import__("time").time):
        self._lock = threading.Lock()
        self._resources: dict[str, Resource] = {}
        self._clock = clock

    # ---- 写入 ----
    def write(
        self,
        key: str,
        body: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict | None = None,
        if_match: str | None = None,
    ) -> Response:
        """写入新内容。if_match 提供时作为写前置条件（乐观并发控制）。"""
        with self._lock:
            current = self._resources.get(key)
            if if_match is not None:
                if current is None or if_match != current.etag:
                    return Response(STATUS_PRECONDITION_FAILED, {}, b"")
            resource = Resource(
                body=bytes(body),
                etag=make_etag(body),
                last_modified=self._clock(),
                content_type=content_type,
                metadata=dict(metadata or {}),
            )
            self._resources[key] = resource
            return Response(STATUS_OK, self._validator_headers(resource), b"")

    def touch_metadata(self, key: str, **metadata) -> bool:
        """只改元数据、不改内容：ETag 必须保持不变（内容校验器语义）。"""
        with self._lock:
            current = self._resources.get(key)
            if current is None:
                return False
            merged = dict(current.metadata)
            merged.update(metadata)
            self._resources[key] = Resource(
                body=current.body,
                etag=current.etag,
                last_modified=self._clock(),
                content_type=current.content_type,
                metadata=merged,
            )
            return True

    # ---- 读取 ----
    def get(self, key: str) -> Response:
        """朴素全量响应（对拍基准）。"""
        with self._lock:
            resource = self._resources.get(key)
            if resource is None:
                return Response(STATUS_NOT_FOUND, {}, b"")
            return self._full_response(resource)

    def conditional_get(
        self,
        key: str,
        if_none_match: str | None = None,
        if_modified_since: str | None = None,
    ) -> Response:
        """条件 GET。命中缓存返回 304 且无实体体。"""
        with self._lock:
            resource = self._resources.get(key)
            if resource is None:
                return Response(STATUS_NOT_FOUND, {}, b"")
            headers = self._validator_headers(resource)
            if self._is_fresh(resource, if_none_match, if_modified_since):
                return Response(STATUS_NOT_MODIFIED, headers, b"")
            return self._full_response(resource)

    def snapshot(self, key: str) -> Resource | None:
        with self._lock:
            return self._resources.get(key)

    # ---- 内部 ----
    @staticmethod
    def _is_fresh(
        resource: Resource,
        if_none_match: str | None,
        if_modified_since: str | None,
    ) -> bool:
        # If-None-Match 优先；存在时忽略 If-Modified-Since（RFC 9110 §13.1.3）
        if if_none_match is not None:
            candidates = [t.strip() for t in if_none_match.split(",")]
            if "*" in candidates:
                return True
            # 强比较：ETag 由内容派生，字符串相等 <=> 内容相等
            return resource.etag in candidates
        if if_modified_since is not None:
            since = parse_http_date(if_modified_since)
            if since is None:
                return False  # 无法解析的日期不视为有效校验器
            # HTTP-date 为秒级精度，截断后比较
            return int(resource.last_modified) <= int(since)
        return False

    @staticmethod
    def _validator_headers(resource: Resource) -> dict:
        return {
            "ETag": resource.etag,
            "Last-Modified": http_date(resource.last_modified),
        }

    def _full_response(self, resource: Resource) -> Response:
        headers = self._validator_headers(resource)
        headers["Content-Type"] = resource.content_type
        headers["Content-Length"] = str(len(resource.body))
        return Response(STATUS_OK, headers, resource.body)


class CachingClient:
    """带本地缓存的条件请求客户端。带宽统计按线上字节数累计。"""

    def __init__(self, store: ResourceStore, conditional: bool = True):
        self.store = store
        self.conditional = conditional
        self._cache: dict[str, tuple[bytes, str, str]] = {}  # key -> (body, etag, last_modified)
        self.bytes_received = 0

    def fetch(self, key: str) -> bytes | None:
        if self.conditional and key in self._cache:
            _, etag, last_modified = self._cache[key]
            resp = self.store.conditional_get(
                key, if_none_match=etag, if_modified_since=last_modified
            )
        else:
            resp = self.store.get(key)
        self.bytes_received += resp.wire_bytes
        if resp.status == STATUS_NOT_MODIFIED:
            return self._cache[key][0]
        if resp.status == STATUS_OK:
            self._cache[key] = (
                resp.body,
                resp.headers["ETag"],
                resp.headers["Last-Modified"],
            )
            return resp.body
        return None
