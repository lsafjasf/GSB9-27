"""旧版（有缺陷）的采样实现，仅用于复现问题。

缺陷：为了把总体流量降到固定比例，对所有记录——包括错误与超阈值记录——
使用同一个采样比例。故障时错误记录占比很低，又按同一比例被丢弃，
导致监控面板里看不到任何错误样本，丢失排查现场。
"""

from __future__ import annotations

import random
from typing import Iterable, List, Tuple

from sampler import Record


class LegacySampler:
    def __init__(self, rate: float = 0.1, *, seed: object = 0) -> None:
        if not 0.0 <= float(rate) <= 1.0:
            raise ValueError(f"rate must be within [0, 1], got {rate!r}")
        self.rate = float(rate)
        self.seed = seed

    def sample(self, records: Iterable[Record], *, seed: object = ...) -> Tuple[List[Record], List[Record]]:
        if seed is ...:
            seed = self.seed
        rng = random.Random(seed)
        kept: List[Record] = []
        dropped: List[Record] = []
        for record in records:
            keep = rng.random() < self.rate
            (kept if keep else dropped).append(record)
        return kept, dropped
