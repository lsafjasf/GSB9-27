# GSB9-27 测试夹具状态污染修复

## 污染源清单（fixture_buggy.py）

| 共享可变状态 | 类型 | 写入方 | 受害方 |
|---|---|---|---|
| `TMPDIR`（进程级共享临时目录） | 文件系统 | `test_06_write_work_file`、`test_08_boom_leaves_mess` 写文件 | `test_03_tmpdir_is_empty` |
| `REGISTRY`（模块级全局注册表 dict） | 内存全局 | `SharedFixture.setup` 写 `initialized`；`test_07_register_plugin`、`test_08` 写键 | `test_02_registry_has_only_baseline` |
| `COUNTER`（模块级共享计数） | 内存全局 | 每个用例的 `setup` 自增且永不复位 | `test_01_counter_starts_at_one` |
| `os.environ[GSB_FIXTURE_PROFILE]` | 进程环境变量 | `test_05_configure_env` 写入不清理 | `test_04_env_not_leaked` |
| `LOOKUP_CACHE`（模块级 memoization 缓存 dict） | 内存全局 | `test_10_populate_cache` 经 `cached_lookup()` 填充且永不失效 | `test_09_cache_starts_cold` |

根因：`SharedFixture.teardown` 是空操作——无目录隔离、无快照/还原、
不清空模块级缓存，失败路径（`test_08` 抛异常）同样残留污染。

## 修复方案（fixture_fixed.py）

- 临时目录：每用例 `tempfile.mkdtemp` 独立目录，`close()` 中 `shutil.rmtree`
- 注册表/计数器：改为每用例实例属性（`fx.registry` / `fx.counter`），不再共享
- 缓存：改为每用例实例属性 `fx.lookup_cache`，随夹具销毁，不再跨用例复用
- 环境变量：`fx.set_env()` 记录原值（含"原本不存在"），`close()` 精确还原；
  声明 `uses_env=True` 的用例在 `ENV_LOCK` 下串行化，避免并发互相观测
- 失败路径：所有还原在 `__exit__`/`close()` 中执行，异常与 `SkipTest` 同样生效；
  测试侧用 `addCleanup(fx.close)` 兜底，`close()` 幂等

## 运行命令

```bash
python3 stability_runner.py        # 复现 + 顺序无关性 + 并发一致性（总入口）
python3 -m unittest discover -v    # 修复套件 + 清理回归测试（16 个用例）
python3 -m unittest repro_buggy_suite.OrderDependentSuite.test_03_tmpdir_is_empty  # 单跑复现用例：绿
```

## 复现套件状态（两种跑法，结论分开）

- **逐条单跑**（每条一个全新进程）：`test_01`~`test_07`、`test_09`、`test_10` 绿；
  `test_08_boom_leaves_mess` 无条件抛 `RuntimeError`，永远红。
  即本套件**不存在全绿状态**，"单跑全绿"的说法不成立。
- **整套跑**（同一进程全量/乱序）：`test_08` 必然 error；共享状态跨用例残留，
  受害者用例（`test_01`/`02`/`03`/`04`/`09`）随执行顺序随机失败。

## 稳定性数据

### A. 复现（缺陷套件，6 轮随机乱序）
每轮失败集合随顺序变化（5~6 个用例未通过，6 轮产生 2 种不同结果分布）：

```
轮次 0:   test_01, test_02, test_03, test_08, test_09            (5 个)
轮次 1-5: test_01, test_02, test_03, test_04, test_08, test_09   (6 个)
```
其中 `test_09_cache_starts_cold` 每轮必挂：乱序跑时 `test_10` 一旦执行过，
模块级缓存即被永久填充，同进程后续轮次无法回到冷缓存状态。

### B. 顺序无关性（修复套件）
- 20 轮随机乱序（另验证过 50 轮、不同种子）：**各轮结果完全一致，10/10 全绿**

### C. 并发一致性（修复套件）
- 串行结果：10/10 pass
- 并发（ThreadPoolExecutor, workers=8/16）× 10 轮：**与串行结果完全一致**

### D. 失败路径清理断言（test_cleanup.py，6 个用例）
- `test_cleanup_on_exception`：用例抛 `RuntimeError` 后，临时目录已删除、环境变量已还原
- `test_cleanup_on_skip`：用例 `SkipTest` 后，同样还原
- `test_env_preexisting_value_restored`：预先存在的 env 值被还原而非删除
- `test_close_is_idempotent`：`close()` 可重复调用
- `test_cache_is_per_fixture`：新夹具的缓存为冷缓存，不继承上一夹具的条目
- `test_concurrent_fixtures_do_not_interfere`：16 线程屏障同步下各自读写互不干扰，退出后全部还原

## 文件

- `fixture_buggy.py` — 有缺陷的夹具（污染源现场）
- `repro_buggy_suite.py` — 复现套件（文件名故意避开 unittest discover）
- `fixture_fixed.py` — 修复后的夹具
- `test_fixed_suite.py` — 同构修复套件（任意顺序/并发全绿）
- `test_cleanup.py` — 清理与并发隔离回归测试
- `stability_runner.py` — 稳定性/并发验证运行器
