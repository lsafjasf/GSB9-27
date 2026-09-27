"""字段级加密库（仅标准库）。

设计目标：
- 等值查询：确定性分组标签（blind index），标签内嵌密钥版本，支持轮换后重建。
- 可逆密文：Encrypt-then-MAC，密文带完整性校验，并通过 AAD 绑定关联信息
  （表/字段/记录主键），跨记录复制密文可被检测。
- 仅依赖 Python 标准库：HMAC-SHA256 + HKDF + 计数器模式密钥流。

密文信封格式（字节序均为大端）：
    "FC1" | key_version(4B) | nonce(16B) | ciphertext(N) | mac(32B)
标签格式：
    "TG1" | key_version(4B) | HMAC-SHA256(tag_key, "idx"||0x00||context||0x00||normalize(pt))[:16]

密钥派生（每个密钥版本一把 32 字节主密钥）：
    enc_key = HKDF-SHA256(master, info="fieldcipher/enc")
    mac_key = HKDF-SHA256(master, info="fieldcipher/mac")
    tag_key = HKDF-SHA256(master, info="fieldcipher/tag")

注意：本库的对称加密是「HMAC-DRBG 密钥流 + Encrypt-then-MAC」的自研构造，
仅为满足"仅标准库"约束。生产环境若有条件，应替换为 AES-GCM / ChaCha20-Poly1305
（接口保持不变即可）。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import struct
import unicodedata
from dataclasses import dataclass

MAGIC_CT = b"FC1"
MAGIC_TAG = b"TG1"
KEY_VERSION_LEN = 4
NONCE_LEN = 16
TAG_TRUNC_LEN = 16   # 标签截断到 128 bit
MAC_LEN = 32
DEFAULT_MAX_PLAINTEXT = 1 << 16  # 64 KiB，超长字段拒绝写入

_HEADER = struct.Struct(">3sI16s")  # magic, key_version, nonce


class FieldCipherError(Exception):
    """库内所有异常的基类。"""


class IntegrityError(FieldCipherError):
    """密文被篡改、或 AAD 不匹配（如跨记录复制密文）。"""


class UnknownKeyVersion(FieldCipherError):
    """密文/标签引用的密钥版本不在钥匙串中（例如旧密钥已退役）。"""


class FieldTooLong(FieldCipherError):
    """明文超过 max_plaintext 限制。"""


def _hkdf_sha256(ikm: bytes, info: bytes, length: int = 32, salt: bytes = b"") -> bytes:
    """RFC 5869 HKDF-Extract+Expand（HMAC-SHA256）。"""
    prk = hmac.new(salt or b"\x00" * 32, ikm, hashlib.sha256).digest()
    okm, block, counter = b"", b"", 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def _keystream(key: bytes, nonce: bytes, nbytes: int) -> bytes:
    """HMAC-SHA256 计数器模式密钥流。同一 (key, nonce) 绝不允许加密两条消息。"""
    out = bytearray()
    counter = 0
    while len(out) < nbytes:
        out += hmac.new(key, nonce + struct.pack(">Q", counter), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:nbytes])


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


@dataclass(frozen=True)
class KeyRing:
    """版本化钥匙串。keys: {version: 32字节主密钥}，active 为当前写入版本。"""

    keys: dict[int, bytes]
    active: int

    def __post_init__(self) -> None:
        if self.active not in self.keys:
            raise ValueError("active version not present in keys")
        for version, key in self.keys.items():
            if not isinstance(version, int) or version < 0:
                raise ValueError("key version must be a non-negative int")
            if len(key) < 16:
                raise ValueError("master key too short (need >= 16 bytes, recommend 32)")

    def key_for(self, version: int) -> bytes:
        try:
            return self.keys[version]
        except KeyError:
            raise UnknownKeyVersion(f"key version {version} not in keyring") from None


class _VersionKeys:
    def __init__(self, master: bytes) -> None:
        self.enc = _hkdf_sha256(master, b"fieldcipher/enc")
        self.mac = _hkdf_sha256(master, b"fieldcipher/mac")
        self.tag = _hkdf_sha256(master, b"fieldcipher/tag")


class FieldCipher:
    """单个字段（列）的加解密器。

    context: 字段绑定标识，如 b"users.email"。参与标签计算实现域分隔，
             不同字段的同值明文不会产生相同标签。
    max_plaintext: 明文（UTF-8 编码后）最大字节数，超出抛 FieldTooLong。
    pad_to: 可选的长度分桶（如 256），密文长度对齐到该倍数，缓解长度泄露。
    case_sensitive: False 时标签计算前做 NFKC + casefold 归一化
                    （密文始终保留原始明文，归一化只影响等值标签）。
    """

    def __init__(
        self,
        keyring: KeyRing,
        context: bytes,
        *,
        max_plaintext: int = DEFAULT_MAX_PLAINTEXT,
        pad_to: int | None = None,
        case_sensitive: bool = False,
    ) -> None:
        if not context:
            raise ValueError("context must be non-empty")
        if pad_to is not None and pad_to < 8:
            raise ValueError("pad_to must be >= 8")
        self._keyring = keyring
        self._context = context
        self._max_plaintext = max_plaintext
        self._pad_to = pad_to
        self._case_sensitive = case_sensitive
        self._derived: dict[int, _VersionKeys] = {}

    # ---- 内部 ----

    def _keys(self, version: int) -> _VersionKeys:
        if version not in self._derived:
            self._derived[version] = _VersionKeys(self._keyring.key_for(version))
        return self._derived[version]

    def _normalize(self, value: str) -> str:
        text = unicodedata.normalize("NFKC", value)
        return text if self._case_sensitive else text.casefold()

    def _encode(self, value: str) -> bytes:
        raw = value.encode("utf-8")
        if len(raw) > self._max_plaintext:
            raise FieldTooLong(
                f"plaintext is {len(raw)} bytes, limit is {self._max_plaintext}"
            )
        frame = struct.pack(">I", len(raw)) + raw
        if self._pad_to:
            pad = (-len(frame)) % self._pad_to
            frame += b"\x00" * pad
        return frame

    def _decode(self, frame: bytes) -> str:
        if len(frame) < 4:
            raise IntegrityError("ciphertext payload too short")
        (length,) = struct.unpack(">I", frame[:4])
        body, padding = frame[4 : 4 + length], frame[4 + length :]
        if len(body) != length or any(padding):
            raise IntegrityError("ciphertext framing corrupted")
        return body.decode("utf-8")

    # ---- 等值标签 ----

    def tag_for(self, value: str | None) -> bytes | None:
        """计算等值查询标签（使用当前 active 密钥版本）。None -> None（存 NULL）。"""
        if value is None:
            return None
        keys = self._keys(self._keyring.active)
        digest = hmac.new(
            keys.tag,
            b"idx\x00" + self._context + b"\x00" + self._normalize(value).encode("utf-8"),
            hashlib.sha256,
        ).digest()[:TAG_TRUNC_LEN]
        return MAGIC_TAG + struct.pack(">I", self._keyring.active) + digest

    # ---- 加解密 ----

    def encrypt(self, value: str | None, aad: bytes = b"") -> tuple[bytes | None, bytes | None]:
        """返回 (密文, 标签)。value 为 None 时返回 (None, None)，表示数据库 NULL。"""
        if value is None:
            return None, None
        version = self._keyring.active
        keys = self._keys(version)
        frame = self._encode(value)
        nonce = os.urandom(NONCE_LEN)
        ciphertext = _xor(frame, _keystream(keys.enc, nonce, len(frame)))
        header = _HEADER.pack(MAGIC_CT, version, nonce)
        mac = hmac.new(
            keys.mac,
            header + struct.pack(">Q", len(aad)) + aad + ciphertext,
            hashlib.sha256,
        ).digest()
        return header + ciphertext + mac, self.tag_for(value)

    def decrypt(self, blob: bytes | None, aad: bytes = b"") -> str | None:
        if blob is None:
            return None
        if len(blob) < _HEADER.size + 4 + MAC_LEN:
            raise IntegrityError("ciphertext too short")
        magic, version, nonce = _HEADER.unpack(blob[: _HEADER.size])
        if magic != MAGIC_CT:
            raise IntegrityError("bad ciphertext magic")
        keys = self._keys(version)  # 未知版本在此抛 UnknownKeyVersion
        ciphertext, mac = blob[_HEADER.size : -MAC_LEN], blob[-MAC_LEN:]
        expected = hmac.new(
            keys.mac,
            blob[: _HEADER.size] + struct.pack(">Q", len(aad)) + aad + ciphertext,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(mac, expected):
            raise IntegrityError("MAC mismatch: tampered ciphertext or wrong AAD")
        frame = _xor(ciphertext, _keystream(keys.enc, nonce, len(ciphertext)))
        return self._decode(frame)

    # ---- 轮换 ----

    def rotate_value(
        self, blob: bytes | None, aad: bytes = b""
    ) -> tuple[bytes | None, bytes | None]:
        """用 active 版本重新加密一条记录。幂等：重复执行结果仍可读。"""
        if blob is None:
            return None, None
        return self.encrypt(self.decrypt(blob, aad), aad)


def make_aad(table_field: str, record_id: str | int) -> bytes:
    """构造关联数据：把密文绑定到 表.字段 + 记录主键，防止跨记录复制。"""
    return f"aad\x00{table_field}\x00{record_id}".encode("utf-8")


def rotate_table(
    conn: sqlite3.Connection,
    cipher: FieldCipher,
    *,
    table: str,
    id_column: str,
    ct_column: str,
    tag_column: str,
    table_field: str,
    batch_size: int = 500,
    should_abort=None,
) -> int:
    """分批、可断点续跑的表级密钥轮换。

    每批一个事务；崩溃后重跑即可：已轮换的行（密钥版本 == active）会被跳过，
    未轮换的行继续处理。返回本次轮换的行数。
    should_abort: 可选回调，每批提交前调用，返回 True 则模拟中断（用于演练/测试）。
    """
    active = cipher._keyring.active
    rotated = 0
    while True:
        rows = conn.execute(
            f"SELECT {id_column}, {ct_column} FROM {table} "
            f"WHERE {ct_column} IS NOT NULL AND "
            f"(substr({ct_column}, 4, 4) <> ? OR {tag_column} IS NULL) "
            f"ORDER BY {id_column} LIMIT ?",
            (struct.pack(">I", active), batch_size),
        ).fetchall()
        if not rows:
            return rotated
        if should_abort is not None and should_abort():
            raise InterruptedError("rotation aborted (simulated crash)")
        with conn:  # 单事务提交一批；崩溃只影响本批，重跑幂等
            for record_id, blob in rows:
                aad = make_aad(table_field, record_id)
                new_ct, new_tag = cipher.rotate_value(blob, aad)
                conn.execute(
                    f"UPDATE {table} SET {ct_column} = ?, {tag_column} = ? "
                    f"WHERE {id_column} = ?",
                    (new_ct, new_tag, record_id),
                )
                rotated += 1
