# 平滑预测库（纯 Python 3 标准库）

对容量类时间序列做滚动预测：输出未来若干点的预测值与预测区间，并用滚动
回测验证区间覆盖率。仅依赖标准库（`math` / `statistics` / `dataclasses`）。

## 文件

- `forecast.py` — 库源码（模型、自动拟合、预测区间、滚动回测）
- `backtest.py` — 回测脚本：多场景 × 多参数配置的误差与覆盖率对比
- `grid_counts.py` — 统计默认/细网格实际评估的候选组合数与耗时比
- `test_forecast.py` — 自测（unittest，19 个用例）

## 运行命令

```bash
python3 -m unittest test_forecast -v   # 自测
python3 backtest.py                    # 回测 + 覆盖率报告
python3 grid_counts.py                 # 网格候选数与耗时比（真实数字来源）
```

## 快速上手

```python
from forecast import forecast, rolling_backtest

fc = forecast(history, horizon=8, season_length=12, confidence=0.95)
fc.values    # 未来 8 点预测值
fc.lower     # 95% 预测区间下界（容量是否够用的判断依据）
fc.upper     # 95% 预测区间上界
fc.model     # 自动选出的模型: ses / holt / hw-add / hw-mul
fc.params    # 拟合出的 alpha/beta/gamma
fc.warnings  # 降级与告警信息（短序列、常数、趋势突变等）

bt = rolling_backtest(history, horizon=8, season_length=12)
bt.mae, bt.rmse, bt.mape, bt.coverage   # 回测误差与区间覆盖率
```

## 模型与拟合目标

支持三种平滑成分，按数据自动选择：

| 模型 | 成分 | 适用 |
|---|---|---|
| `ses` | 水平 | 平稳序列 |
| `holt` | 水平 + 趋势 | 有增长/下降趋势 |
| `hw-add` / `hw-mul` | 水平 + 趋势 + 季节（加法/乘法） | 有周期波动 |

**拟合目标**：在参数网格上搜索 `(alpha, beta, gamma)`，最小化训练序列上的
**一步前向预测误差平方和（one-step-ahead SSE）**——即回测误差，而非极大似然。
季节为加法还是乘法也由同一目标自动比较选出（`seasonal="auto"`）。

**预测区间**：`点预测 ± z(confidence) × sigma_h`。`sigma_h` 是**每个预测步长
h 各自的误差标准差**，用拟合好的参数在训练序列内部做一次滚动起点回测估出
（样本不足 5 个时退化为 `sigma_1 * sqrt(h)`）。相比固定的理论方差公式，它
直接反映该序列上该模型的真实外推误差，覆盖率更贴近置信水平。

## 参数说明

| 参数 | 默认 | 说明 |
|---|---|---|
| `horizon` | 必填 | 预测点数 |
| `season_length` | `None` | 季节周期（如月度周期取 12）；`None` 表示无季节成分 |
| `confidence` | `0.95` | 预测区间置信水平，z 值由 `statistics.NormalDist` 计算 |
| `seasonal` | `"auto"` | 季节类型：`auto` / `add` / `mul` |
| `grid` | `DEFAULT_GRID` | 平滑参数候选值，默认每参数 8 个值 `(0.05..0.9)`；细网格 `FINE_GRID` 为 13 个值 |
| `fixed_params` | `None` | 指定 `(alpha, beta, gamma)` 跳过网格搜索（用于对比/复现） |

**默认参数选择依据**（见下方回测数据）：

- 默认网格 vs 细网格：误差与覆盖率几乎相同（RMSE 2.215 vs 2.199）。
  自动路径对每个候选网格（大小 g）评估 `g + g² + 2g³` 个组合（SES g 个、
  Holt g² 个、加法/乘法 Holt-Winters 各 g³ 个），由 `grid_counts.py` 实测：
  默认 1096 个 vs 细网格 4576 个（4.18 倍），实测网格搜索耗时也约为 4 倍
  （计时随机器波动，以候选数为准），故选粗网格为默认。
- 自动拟合 vs 固定经典参数 `(0.3, 0.1, 0.1)`：自动拟合在平稳场景 RMSE
  明显更优（2.215 vs 2.909）；固定参数仅在趋势突变场景更好（自适应
  更快），但该场景会触发告警，故不作为默认。
- 季节成分不可省略：去掉后 MAPE 从 1.8% 涨到 14.9%。

## 降级与告警

| 情形 | 行为 |
|---|---|
| 空序列 | 抛 `ValueError` |
| 单点 / 常数序列 | 平推预测、零宽区间，附 warning |
| `n < 4` | 退化为均值预测，`sqrt(h)` 区间，附 warning |
| `n < 2 * season_length` | 丢弃季节成分，附 warning |
| `n < 6` | 不用趋势成分（仅 SES） |
| 趋势/水平突变 | 最近 1/4 窗口的一步误差均值 > 历史 2.5 倍时发出 structural-change 告警 |
| 乘法季节不可行（初始两季含非正值） | `seasonal="auto"` 时直接跳过乘法候选（无警告，加法仍参与比较）；显式 `seasonal="mul"`（含 `fixed_params`）时回退加法并附 warning |
| 乘法拟合中途不可行（季节指数变 0） | 仅显式 `seasonal="mul"` 可达：回退加法并附 warning（auto 路径该候选已在搜索中被跳过） |

## 回测与覆盖率数据（`python3 backtest.py`，horizon=8，95% 区间）

| 场景 | 配置 | MAE | RMSE | MAPE% | 覆盖率 |
|---|---|---|---|---|---|
| 趋势+季节 (n=120, m=12) | auto 默认网格 | 1.724 | 2.215 | 1.81 | 93.0% |
| | auto 细网格 | 1.713 | 2.199 | 1.80 | 92.5% |
| | 固定 0.3/0.1/0.1 | 2.259 | 2.909 | 2.34 | 91.7% |
| | 无季节成分 | 14.346 | 19.371 | 14.91 | 98.7% |
| 纯水平 (n=100) | auto 默认网格 | 1.939 | 2.465 | 3.93 | 92.6% |
| | 固定 0.3/0.1/0.1 | 2.228 | 2.814 | 4.50 | 92.0% |
| 趋势突变 @t=80 (n=120) | auto 默认网格 | 3.450 | 4.740 | 5.76 | 70.6% |
| | 固定 0.3/0.1/0.1 | 2.351 | 3.209 | 3.86 | 85.5% |
| 常数 (n=80) | 任意 | 0.000 | 0.000 | 0.00 | 100.0% |
| 超短 (n=10) | auto 默认网格 | 4.056 | 4.209 | 12.16 | 33.3%（仅 2 个回测原点，不具统计意义） |

结论：

- 数据规律稳定时，覆盖率 92–93%，与 95% 置信水平接近（略保守方向偏差
  来自区间 sigma 用全样本拟合参数估计）。
- 趋势突变时覆盖率降到 ~71%：平滑模型本质上无法预知结构性变化，此时
  依赖 structural-change 告警提示人工介入，而不是假装区间仍可信。
- 容量判读建议用区间**上界**（`fc.upper`）对比容量上限，留出突变余量。
