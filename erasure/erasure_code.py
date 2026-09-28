"""分块纠删码：GF(2^8) 上的 Reed-Solomon（Cauchy 编码矩阵）。

把原始数据切成 k 个数据块 D0..D{k-1}，再生成 m 个校验块 P0..P{m-1}：

    [D]                       I 是 k*k 单位阵
    [ ] = G * D,  G = [ I ; Cauchy(m, k) ]
    [P]

G 的任意 k 行都线性无关（Cauchy 性质），所以 k+m 个块中
任意 m 个块（数据块或校验块均可）同时损坏都能精确恢复；
损坏超过 m 个则抛出 UnrecoverableError。

仅依赖 Python 3 标准库。大字节运算通过 int 异或和
bytes.translate 的乘法查表加速，避免逐字节 Python 循环。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

GF_EXP_BASE = 0x11D  # GF(2^8) 本原多项式 x^8+x^4+x^3+x^2+1


class UnrecoverableError(Exception):
    """存活块少于 k 个（损坏数超过可容忍上限 m），无法恢复。"""


# ---------- GF(256) 运算 ----------

def _build_gf_tables():
    exp_table = [0] * 512
    log_table = [0] * 256
    value = 1
    for i in range(255):
        exp_table[i] = value
        log_table[value] = i
        value <<= 1
        if value & 0x100:
            value ^= GF_EXP_BASE
    for i in range(255, 512):
        exp_table[i] = exp_table[i - 255]
    return exp_table[:255], log_table


_EXP, _LOG = _build_gf_tables()


def gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[(_LOG[a] + _LOG[b]) % 255]


def gf_inv(a: int) -> int:
    if a == 0:
        raise ZeroDivisionError("GF(256) 中 0 没有逆元")
    return _EXP[(255 - _LOG[a]) % 255]


def gf_mul_vec(data: bytes, coef: int) -> bytes:
    """整块标量乘法 coef * data，使用 256 项查表 + translate。"""
    if coef == 0:
        return bytes(len(data))
    if coef == 1:
        return bytes(data)
    table = bytes(gf_mul(coef, x) for x in range(256))
    return data.translate(table)


# ---------- 编码矩阵 ----------

def coding_matrix(k: int, m: int):
    """完整 (k+m) x k 编码矩阵：上半部单位阵，下半部 Cauchy。"""
    if not (1 <= k <= 255 and 1 <= m and k + m <= 256):
        raise ValueError("要求 1 <= k <= 255, m >= 1 且 k+m <= 256")
    matrix = [[0] * k for _ in range(k + m)]
    for i in range(k):
        matrix[i][i] = 1
    for row in range(m):
        matrix[k + row] = [gf_inv(row ^ (k + col)) for col in range(k)]
    return matrix


# ---------- 编 / 解码 ----------

@dataclass
class Encoded:
    k: int
    m: int
    original_size: int          # 原始数据字节数（最后一块可能被填充）
    block_size: int             # 每块统一长度（零长度数据时为 0）
    checksums: list             # blocks[i] 的 sha256，bytes
    blocks: list                # k 个数据块 + m 个校验块，均为 bytes

    def tolerable_losses(self) -> int:
        """可容忍同时损坏的块数上限。"""
        return self.m


def encode(data: bytes, k: int, m: int) -> Encoded:
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data 必须是 bytes/bytearray")
    matrix = coding_matrix(k, m)

    if len(data) == 0:
        empty = bytes()
        return Encoded(k, m, 0, 0,
                       [hashlib.sha256(empty).digest()] * (k + m),
                       [empty] * (k + m))

    block_size = (len(data) + k - 1) // k
    padded = bytes(data) + bytes(block_size * k - len(data))
    data_blocks = [padded[i * block_size:(i + 1) * block_size] for i in range(k)]

    blocks = list(data_blocks)
    for row in range(m):
        acc = bytes(block_size)
        for col, coef in enumerate(matrix[k + row]):
            term = gf_mul_vec(data_blocks[col], coef)
            acc = (int.from_bytes(acc, "big") ^
                   int.from_bytes(term, "big")).to_bytes(block_size, "big")
        blocks.append(acc)

    checksums = [hashlib.sha256(b).digest() for b in blocks]
    return Encoded(k, m, len(data), block_size, checksums, blocks)


def _is_intact(block: bytes, checksum: bytes) -> bool:
    return hashlib.sha256(block).digest() == checksum


def decode(encoded: Encoded, blocks: list) -> bytes:
    """从（可能部分损坏/缺失的）blocks 恢复原始数据。

    blocks[i] 为第 i 块的内容；None 表示该块缺失。
    校验和不匹配视为损坏，同样丢弃。存活块不足 k 个则报错。
    """
    k, m = encoded.k, encoded.m
    if len(blocks) != k + m:
        raise ValueError(f"blocks 长度应为 {k + m}")

    if encoded.original_size == 0:
        lost = sum(
            1 for i, block in enumerate(blocks)
            if block is None or not _is_intact(block, encoded.checksums[i])
        )
        if lost > m:
            raise UnrecoverableError(
                f"零长度数据损坏/缺失 {lost} 块，超过可容忍上限 {m}")
        return b""

    available = [
        i for i, block in enumerate(blocks)
        if block is not None and _is_intact(block, encoded.checksums[i])
    ]
    if len(available) < k:
        lost = (k + m) - len(available)
        raise UnrecoverableError(
            f"需要至少 {k} 个完好块，仅存 {len(available)} 个；"
            f"损坏/缺失 {lost} 块，超过可容忍上限 {m}")

    available = available[:k]  # 任意 k 个完好块即可
    full = coding_matrix(k, m)
    sub = [full[i] for i in available]

    # 增广矩阵 [A | 存活块] 上做 GF(256) Gauss-Jordan 消元，
    # 左侧化为单位阵后右侧即为全部 k 个数据块。
    size = encoded.block_size
    rhs = [bytes(blocks[i]) for i in available]
    for col in range(k):
        pivot = next((r for r in range(col, k) if sub[r][col] != 0), None)
        if pivot is None:  # 理论上不会发生（Cauchy 任意子阵可逆）
            raise UnrecoverableError("编码矩阵子阵奇异，无法恢复")
        if pivot != col:
            sub[col], sub[pivot] = sub[pivot], sub[col]
            rhs[col], rhs[pivot] = rhs[pivot], rhs[col]

        inv = gf_inv(sub[col][col])
        sub[col] = [gf_mul(v, inv) for v in sub[col]]
        rhs[col] = gf_mul_vec(rhs[col], inv)

        pivot_row = sub[col]
        for r in range(k):
            if r == col:
                continue
            factor = sub[r][col]
            if factor == 0:
                continue
            sub[r] = [
                a ^ gf_mul(b, factor)
                for a, b in zip(sub[r], pivot_row)
            ]
            term = gf_mul_vec(rhs[col], factor)
            rhs[r] = (int.from_bytes(rhs[r], "big") ^
                      int.from_bytes(term, "big")).to_bytes(size, "big")

    recovered = b"".join(rhs[:k])
    return recovered[:encoded.original_size]
