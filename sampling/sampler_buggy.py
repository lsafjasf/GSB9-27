"""有缺陷的原始实现：对所有记录做均匀采样。

故障复现点：错误记录与正常记录按同样比例被丢弃，
监控面板在故障时段只能看到 ~p 比例的错误样本，排查时缺少现场。
仅保留于此作为回归对照，请勿在生产使用。
"""

import random


class UniformSampler:
    """对所有类别记录按同一比例 p 采样（缺陷版本）。"""

    def __init__(self, seed, rate):
        self._rng = random.Random(seed)
        self._rate = rate

    def keep(self, record):
        # 缺陷：不区分类别，错误记录同样被按比例丢弃
        return self._rng.random() < self._rate
