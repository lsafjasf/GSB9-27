"""abstats: 纯标准库实现的 A/B 检验与功效分析工具。

子模块
------
dist  : 正态 / Student-t / F 分布函数
test  : 两组均值差异检验、置信区间、前提校验
power : 样本量规划与功效模拟
multicomp : 多重比较校正 (Bonferroni / Holm / BH-FDR)
mannwhitney : Mann-Whitney U (确切检验 + 正态近似)
permutation : 置换检验 (确切 / 蒙特卡洛)
"""

from . import dist, test, power, multicomp, mannwhitney, permutation

__all__ = ["dist", "test", "power", "multicomp", "mannwhitney", "permutation"]
__version__ = "1.0.0"
