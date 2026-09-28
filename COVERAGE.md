# 过期可见性修复：覆盖清单

## 一、读取路径清单（每条都必须经过统一过期门 `_expired_locked`）

| # | 读取路径 | API | 是否执行过期判断 | 实现位置 | 回归测试 |
|---|----------|-----|------------------|----------|----------|
| 1 | 点查 | `get(k, default)` | 是 | `store_fixed.py` get | `test_point_get_expires_on_boundary` |
| 2 | 批量点查 | `get_many(keys)` | 是（逐 key） | get_many | `test_batch_scan_many_expired_mixed`、`test_correct_without_any_purge` |
| 3 | 存在性判断 | `__contains__` / `in` | 是 | __contains__ | `test_correct_without_any_purge` |
| 4 | 剩余 TTL | `ttl_of(k)` | 是 | ttl_of | `test_correct_without_any_purge` |
| 5 | 范围/前缀扫描 | `scan(start,end,prefix)` | 是（逐行，惰性求值） | scan | `test_scan_filters_expired_range_and_prefix` |
| 6 | 键迭代器 | `keys()` / `__iter__` | 是（经 scan） | keys | `test_iterators_hide_expired_mid_iteration`、`test_randomized_visibility_invariant` |
| 7 | 值迭代器 | `values()` | 是（经 scan） | values | `test_correct_without_any_purge` |
| 8 | 键值迭代器 | `items()` | 是（经 scan） | items | `test_correct_without_any_purge` |
| 9 | 计数聚合 | `count()` / `__len__` | 是（经 scan） | count | `test_aggregates_exclude_expired` |
| 10 | 求和 | `sum(field)` | 是（经 scan） | sum | `test_aggregates_exclude_expired` |
| 11 | 平均 | `avg(field)` | 是（经 scan） | avg | `test_aggregates_exclude_expired` |
| 12 | 最小 | `min(field)` | 是（经 scan） | min | `test_aggregates_exclude_expired` |
| 13 | 最大 | `max(field)` | 是（经 scan） | max | `test_aggregates_exclude_expired` |

`scan` 对快照中的每个 key 在 yield 前单独加锁并调用 `_expired_locked`，
因此迭代过程中到期（#6-8）或被后台清理的行也不会被吐出。

## 二、语义与边界场景

| 场景 | 修复前行为 | 修复后行为 | 回归测试 |
|------|-----------|-----------|----------|
| 边界时刻 `now == expire_at` | get 判过期，其余路径泄漏 | 所有路径判为过期 | `test_point_get_expires_on_boundary` |
| 时钟回拨（已观察到过期） | 行仍在内存，回拨后复活 | `dead` 粘性标记，不可复活 | `test_clock_rollback_cannot_resurrect_observed_expiry` |
| 时钟回拨（未观察到过期） | 正常 | 未到期仍可读 | `test_clock_rollback_before_observation_keeps_live` |
| 写入即过期（ttl<=0） | get 不可见、其他路径可见 | 所有路径立即可见性为“不存在”，但物理保留待清理 | `test_put_with_non_positive_ttl_is_expired_on_arrival` |
| 显式续期 | touch 只改时间 | `put/touch` 同时清除 `dead`，可复活 | `test_explicit_renewal_clears_expiry` |
| 清理任务未运行 | get 顺带删除（读/清理耦合），其余泄漏 | 读取永不删除，逻辑视图始终正确 | `test_correct_without_any_purge` |
| 后台物理清理 | — | `ExpiryJanitor` 周期 purge，与读取解耦 | `test_janitor_cleansup_but_reads_already_correct` |
| 并发删除/清理/读取 | 无锁，可能抛错或泄漏 | RLock + 逐行校验，无异常无泄漏 | `test_concurrent_delete_and_purge_against_readers` |
| 批量扫描（100 键混合 TTL） | 泄漏全部过期行 | 只返回 50 个存活行 | `test_batch_scan_many_expired_mixed` |
| 随机差分测试（2000 轮） | — | put/delete/touch/advance/rollback 下与参考模型一致 | `test_randomized_visibility_invariant` |

## 三、可见性断言

任意时刻、经任意读取路径，均不返回“已过期且未被显式续期”的数据：

- 统一断言：`test_ttl_store.assert_invisible_if_expired` 对一条已到期数据
  遍历全部 13 条读取路径，逐一断言不可见（即使随后把时钟回拨到到期前）。
- 随机差分不变量：`set(store.keys()) == 未到期且未被粘性判死的键集合`，
  且 `store.count() == len(该集合)`，对 2000 轮随机操作成立。
- 并发不变量：扫描过程中 `yield` 的每一行必须满足采样时刻
  `now <= value.deadline`，持续 1 秒、4 线程无违例。

## 四、物理清理解耦

- 读取路径只做逻辑判定并设置 `dead`，**绝不** `del`。
- 唯一物理删除入口：`purge_expired()` 与 `ExpiryJanitor` 周期任务。
- 清理从未运行时：`count()==0` 但 `physical_size()==1`（见
  `test_correct_without_any_purge`），证明正确性不依赖清理。

## 五、写入路径（也统一处理过期）

| 路径 | 语义 |
|------|------|
| `put(k,v,ttl=None)` | ttl=None 永久；ttl<=0 写入即过期（物理占位，逻辑不可见）；重置 `dead` |
| `touch(k,ttl)` | 显式续期，刷新 expire_at 并清除 `dead`；不存在返回 False |
| `delete(k)` | 显式物理删除，返回是否命中 |
