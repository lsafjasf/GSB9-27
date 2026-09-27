# 重试逻辑重构说明

## 结构

- `errors.py` — 共享错误分类；`NON_RETRYABLE_ERRORS` 明确列出禁止重试的类别
  （`ValidationError` 参数非法、`PermissionDeniedError` 权限不足、`NotFoundError` 资源不存在）。
- `retry_policy.py` — 唯一的重试实现：`RetryPolicy`（不可变策略对象，构造时校验参数）
  + `execute(policy, fn)`。
- `legacy/` — 重构前的 13 个模块、15 个调用点（保留用于差分测试对照）。
- `services/` — 重构后的调用点，只声明 `RetryPolicy` 参数。
- `tests/` — 策略单测（`test_retry_policy.py`）+ 逐调用点差分测试（`test_differential.py`）。

## 策略对象参数校验（构造时）

`RetryPolicy.__post_init__` 拒绝：非正整数 `max_attempts`、负 `base_delay`、
小于 1 的 `backoff_multiplier`、负 `max_delay`、超出 [0,1] 的 `jitter`、
非正 `timeout_budget`、空的或非异常类型的 `retryable_errors`，
以及任何会覆盖 `NON_RETRYABLE_ERRORS` 类别的声明——既包括直接列出
`ValidationError` 等，也包括 `Exception`/`ServiceError` 这类会误伤
不可重试子类的过宽类型。

## 调用点参数对照表

| # | 调用点 | max_attempts | base_delay | multiplier | max_delay | jitter | timeout_budget | retryable_errors | 备注 |
|---|--------|---|---|---|---|---|---|---|------|
| 1 | payments.get_balance | 3 | 0.5 | 2.0 | — | — | — | TransientError | |
| 2 | payments.charge | 5 | 0.8 | 2.0 | 8 | — | — | TransientError, RateLimitError | |
| 3 | inventory.reserve | 4 | 0.25 | 3.0 | 6 | 0.5 | — | TransientError | |
| 4 | notifications.send_email | 4 | 0.2 | 2.0 | 3 | — | — | TransientError, RateLimitError | 修正① |
| 5 | usersync.sync_user | 10 | 0.5 | 2.0 | 10 | — | 30s | TransientError, RateLimitError | |
| 6 | reports.export_report | 5 | 1.5 | 2.0 | 20 | 0.5 | 90s | TransientError | |
| 7 | billing.create_invoice | 3 | 0.6 | 2.0 | 5 | — | — | TransientError, RateLimitError | 修正② |
| 8 | search.query | 2 | 0.4 | 2.0 | 1.5 | — | — | TransientError | |
| 9 | webhook.deliver | 6 | 0.4 | 2.0 | 20 | 0.5 | — | TransientError, RateLimitError | |
| 10 | audit.log_event | 3 | 0.15 | 2.0 | 2 | — | — | TransientError, RateLimitError | 修正③ |
| 11 | pricing.get_quote | 3 | 0.3 | 2.0 | 3 | — | — | TransientError | |
| 12 | shipping.create_label | 4 | 0.6 | 2.0 | 8 | — | — | TransientError | 修正④ |
| 13 | session.refresh_token | 2 | 0.25 | 2.0 | 1 | — | — | TransientError | |
| 14 | geo.locate_ip | 3 | 0.4 | 1.0（固定间隔） | — | — | — | RateLimitError | |
| 15 | currency.get_rate | 3 | 0.3 | 2.0 | 5 | — | — | TransientError | |

jitter=0.5 表示实际等待 = 退避等待 × uniform(0.5, 1.5)，与遗留代码公式一致。

## 刻意修正清单（行为变化仅限以下 4 处）

| 修正 | 调用点 | 遗留行为 | 重构后行为 |
|------|--------|----------|------------|
| ① | notifications.send_email | `except Exception`：ValidationError/PermissionDeniedError/NotFoundError 也被重试 4 次 | 仅重试 TransientError/RateLimitError；不可重试错误第 1 次即抛出 |
| ② | billing.create_invoice | `except Exception`：同上，重试 3 次 | 同上，快速失败 |
| ③ | audit.log_event | `except Exception`：同上，重试 3 次 | 同上，快速失败 |
| ④ | shipping.create_label | 把 NotFoundError（404）当可重试，重试 4 次 | 404 快速失败 |

其余 11 个调用点、以及上述 4 个调用点的瞬态/限流场景，重试次数、
逐次等待时间与最终结果与遗留代码完全一致（差分测试逐场景断言）。

## 差分测试方法

`tests/test_differential.py` 对每个调用点注入相同的假时钟
（`time.sleep`/`time.monotonic`）与确定性抖动源（`random.uniform`），
用脚本化 transport 跑 7 种场景（首次成功 / 抖动后成功 / 瞬态耗尽 /
限流 / 参数非法 / 权限不足 / 404），对比重构前后的
`RunResult(attempts, sleeps, outcome)` 三元组。
未修正场景要求完全相等；修正场景断言新行为（attempts=1、sleeps=[]、
异常类型不变），并断言遗留行为确实在重试，作为修正依据。

## 行数对比

| 项 | 行数 |
|----|------|
| 遗留：15 处内联重试循环合计 | 151 行（散落 13 个模块，模块全量 270 行） |
| 重构后：唯一实现 `retry_policy.py` | 106 行（含参数校验与文档；`execute` 循环本体约 25 行） |
| 重构后：15 个调用点（`services/`） | 287 行，其中每处仅 7~9 行声明式策略参数 |

重试循环从 15 份重复实现收敛为 1 份；新增/调整重试参数只需改声明，
不再需要复制循环，也不可能再写出"捕获 Exception 全量重试"的变体
（策略构造时会拒绝覆盖禁止重试类别的声明）。

## 运行命令

```bash
cd /home/administrator/gsb/uid268/A
python3 -m unittest discover -s tests -v   # 全部测试（策略单测 + 差分测试）
python3 -m unittest tests.test_differential -v   # 仅差分测试
python3 -m unittest tests.test_retry_policy -v   # 仅策略单测
```

要求 Python 3.7+，仅标准库。
