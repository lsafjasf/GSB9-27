# 重试逻辑重构说明

## 结构

- `errors.py` — 共享错误类型；`NON_RETRYABLE_ERRORS` 明确列出禁止重试的类别
  （`ValidationError` 参数非法、`PermissionDeniedError` 权限不足、`NotFoundError` 资源不存在）。
- `retry_policy.py` — 唯一的重试实现：`RetryPolicy`（不可变策略对象，构造时校验参数）
  + `execute(policy, fn)`。
- `legacy/` — 重构前的 12 个模块、13 个调用点（保留用于差分测试对照）。
- `services/` — 重构后的调用点，只声明 `RetryPolicy` 参数。
- `tests/` — 策略单测 + 逐调用点差分测试。

## 策略对象参数校验（构造时）

`RetryPolicy.__post_init__` 拒绝：非正整数 `max_attempts`、负 `base_delay`、
小于 1 的 `backoff_multiplier`、负 `max_delay`、超出 [0,1] 的 `jitter`、
非正 `timeout_budget`、空的或非异常类型的 `retryable_errors`，
以及任何包含 `NON_RETRYABLE_ERRORS` 中类别的 `retryable_errors`。

## 调用点参数对照表

| # | 调用点 | max_attempts | base_delay | multiplier | max_delay | jitter | timeout_budget | retryable_errors | 备注 |
|---|--------|---|---|---|---|---|---|---|------|
| 1 | payments.get_balance | 3 | 0.5 | 2.0 | — | — | — | TransientError | |
| 2 | payments.charge | 4 | 1.0 | 2.0 | 10 | — | — | TransientError, RateLimitError | |
| 3 | inventory.reserve | 3 | 0.2 | 3.0 | 5 | 0.5 | — | TransientError | |
| 4 | notifications.send_email | 5 | 0.1 | 2.0 | 2 | — | — | TransientError, RateLimitError | 修正① |
| 5 | usersync.sync_user | 8 | 1.0 | 2.0 | 8 | — | 20s | TransientError, RateLimitError | |
| 6 | reports.export_report | 4 | 2.0 | 2.0 | 30 | 0.5 | 60s | TransientError | |
| 7 | billing.create_invoice | 3 | 0.5 | 2.0 | 4 | — | — | TransientError, RateLimitError | 修正② |
| 8 | search.query | 2 | 0.3 | 2.0 | 1 | — | — | TransientError | |
| 9 | webhook.deliver | 5 | 0.5 | 2.0 | 16 | 0.5 | — | TransientError, RateLimitError | |
| 10 | audit.log_event | 3 | 0.1 | 2.0 | 1 | — | — | TransientError, RateLimitError | 修正③ |
| 11 | pricing.get_quote | 3 | 0.25 | 2.0 | 2 | — | — | TransientError | |
| 12 | shipping.create_label | 4 | 0.75 | 2.0 | 6 | — | — | TransientError | 修正④ |
| 13 | session.refresh_token | 2 | 0.2 | 2.0 | 0.8 | — | — | TransientError | |

jitter=0.5 表示实际等待 = 退避等待 × uniform(0.5, 1.5)，与遗留代码公式一致。

## 刻意修正清单（行为变化仅限以下 4 处）

| 修正 | 调用点 | 遗留行为 | 重构后行为 |
|------|--------|----------|------------|
| ① | notifications.send_email | `except Exception`：ValidationError/PermissionDeniedError/NotFoundError 也被重试 5 次 | 仅重试 TransientError/RateLimitError；不可重试错误第 1 次即抛出 |
| ② | billing.create_invoice | `except Exception`：同上，重试 3 次 | 同上，快速失败 |
| ③ | audit.log_event | `except Exception`：同上，重试 3 次 | 同上，快速失败 |
| ④ | shipping.create_label | 把 NotFoundError（404）当可重试，重试 4 次 | 404 快速失败 |

其余 9 个调用点、以及上述 4 个调用点的瞬态错误场景，重试次数、
逐次等待时间与最终结果与遗留代码完全一致（差分测试逐场景断言）。

## 差分测试方法

`tests/test_differential.py` 对每个调用点注入相同的假时钟
（`time.sleep`/`time.monotonic`）与确定性抖动源（`random.uniform`），
用脚本化 transport 跑 7 种场景（首次成功 / 抖动后成功 / 瞬态耗尽 /
限流 / 参数非法 / 权限不足 / 404），对比重构前后的
`RunResult(attempts, sleeps, outcome)` 三元组。
未修正场景要求完全相等；修正场景断言新行为（attempts=1、sleeps=[]、
异常类型不变），并保留遗留行为作对照。

## 行数对比

| 项 | 行数 |
|----|------|
| 遗留：13 处内联重试循环合计 | 122 行（散落 12 个模块，全量 238 行） |
| 重构后：唯一实现 `retry_policy.py` | 95 行（含参数校验与文档；`execute` 循环本体约 30 行） |
| 重构后：13 个调用点（`services/`） | 254 行，其中每处仅 7~9 行声明式策略参数 |

重试循环从 13 份重复实现收敛为 1 份；新增/调整重试参数只需改声明，
不再需要复制循环。

## 运行命令

```bash
cd /home/administrator/gsb/uid268/B
python3 -m unittest discover -s tests -v   # 全部测试（策略单测 + 差分测试）
python3 -m unittest tests.test_differential -v   # 仅差分测试
python3 -m unittest tests.test_retry_policy -v   # 仅策略单测
```

要求 Python 3.10+，仅标准库。
