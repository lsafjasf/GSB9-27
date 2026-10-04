# 增量 vs 全量 对比数据

由 `python3 scripts/run_scenarios.py` 自动生成。每个场景在仓库的干净临时副本上应用改动，分别执行全量与增量，再用 `compare` 子命令逐检查项对比（状态 + 明细，忽略耗时）。

## 汇总

| 场景 | 模式 | 命中率(执行/总数) | 全量耗时ms | 增量耗时ms | 节省 | 判定(全量/增量) | 一致性 |
|---|---|---|---|---|---|---|---|
| single-src-file | incremental | 3/5 | 2.2 | 1.4 | 36% | PASS/PASS | OK |
| single-test-file | incremental | 2/5 | 2.1 | 1.6 | 24% | PASS/PASS | OK |
| single-data-file | incremental | 1/5 | 1.8 | 0.2 | 89% | PASS/PASS | OK |
| multi-file | incremental | 4/5 | 2.3 | 1.8 | 22% | PASS/PASS | OK |
| docs-only | none | 0/5 | 1.4 | 0.0 | 100% | PASS/PASS | OK |
| config-change | incremental->full | 5/5 | 1.5 | 1.5 | 0% | PASS/PASS | OK |
| unknown-file | incremental->full | 5/5 | 1.5 | 1.5 | 0% | PASS/PASS | OK |
| fault-style | incremental | 3/5 | 2.6 | 2.2 | 15% | FAIL/FAIL | OK |
| fault-syntax | incremental | 3/5 | 2.0 | 1.9 | 5% | FAIL/FAIL | OK |
| fault-test | incremental | 2/5 | 2.6 | 2.0 | 23% | FAIL/FAIL | OK |
| fault-json | incremental | 1/5 | 1.9 | 0.2 | 89% | FAIL/FAIL | OK |

> 说明：演示项目的检查本身在毫秒级，真实仓库中单项检查耗时以分钟计时，命中率直接等比映射为节省时间。`compare` 的一致性判定不比较耗时字段。

## 场景明细

### single-src-file

单文件改动：修改一个源码文件

- changed: `demo/src/calculator.py`
- mode: `incremental`, affected: `py-syntax, py-style, py-tests`
- verdict full/incr: PASS/PASS, compare: CONSISTENT
- logs: `results/single-src-file.full.txt`, `results/single-src-file.incr.txt`, `results/single-src-file.compare.txt`

### single-test-file

单文件改动：修改一个测试文件

- changed: `demo/tests/test_calculator.py`
- mode: `incremental`, affected: `py-syntax, py-tests`
- verdict full/incr: PASS/PASS, compare: CONSISTENT
- logs: `results/single-test-file.full.txt`, `results/single-test-file.incr.txt`, `results/single-test-file.compare.txt`

### single-data-file

单文件改动：修改一个 JSON 数据文件

- changed: `demo/data/users.json`
- mode: `incremental`, affected: `json-validate`
- verdict full/incr: PASS/PASS, compare: CONSISTENT
- logs: `results/single-data-file.full.txt`, `results/single-data-file.incr.txt`, `results/single-data-file.compare.txt`

### multi-file

多文件改动：源码 + 数据 + 文档

- changed: `demo/src/calculator.py, demo/data/users.json, README.md`
- mode: `incremental`, affected: `py-syntax, py-style, py-tests, json-validate`
- verdict full/incr: PASS/PASS, compare: CONSISTENT
- logs: `results/multi-file.full.txt`, `results/multi-file.incr.txt`, `results/multi-file.compare.txt`

### docs-only

仅文档改动（命中忽略规则，零检查）

- changed: `README.md`
- mode: `none`, affected: `(none)`
- verdict full/incr: PASS/PASS, compare: CONSISTENT
- logs: `results/docs-only.full.txt`, `results/docs-only.incr.txt`, `results/docs-only.compare.txt`

### config-change

配置文件改动：precheck.json 变更，必须回退全量

- changed: `precheck.json`
- mode: `incremental->full`, affected: `py-syntax, py-style, py-tests, json-validate, config-validate`
- verdict full/incr: PASS/PASS, compare: CONSISTENT
- logs: `results/config-change.full.txt`, `results/config-change.incr.txt`, `results/config-change.compare.txt`

### unknown-file

无法推断：新增映射之外的文件类型，必须回退全量

- changed: `demo/assets/logo.png`
- mode: `incremental->full`, affected: `py-syntax, py-style, py-tests, json-validate, config-validate`
- verdict full/incr: PASS/PASS, compare: CONSISTENT
- logs: `results/unknown-file.full.txt`, `results/unknown-file.incr.txt`, `results/unknown-file.compare.txt`

### fault-style

故障注入：源码引入超长行，py-style 必须 FAIL 且两种模式输出一致

- changed: `demo/src/calculator.py`
- mode: `incremental`, affected: `py-syntax, py-style, py-tests`
- verdict full/incr: FAIL/FAIL, compare: CONSISTENT
- logs: `results/fault-style.full.txt`, `results/fault-style.incr.txt`, `results/fault-style.compare.txt`

### fault-syntax

故障注入：源码引入语法错误，py-syntax 必须 FAIL 且两种模式输出一致

- changed: `demo/src/strings.py`
- mode: `incremental`, affected: `py-syntax, py-style, py-tests`
- verdict full/incr: FAIL/FAIL, compare: CONSISTENT
- logs: `results/fault-syntax.full.txt`, `results/fault-syntax.incr.txt`, `results/fault-syntax.compare.txt`

### fault-test

故障注入：测试断言错误，py-tests 必须 FAIL 且两种模式输出一致

- changed: `demo/tests/test_calculator.py`
- mode: `incremental`, affected: `py-syntax, py-tests`
- verdict full/incr: FAIL/FAIL, compare: CONSISTENT
- logs: `results/fault-test.full.txt`, `results/fault-test.incr.txt`, `results/fault-test.compare.txt`

### fault-json

故障注入：JSON 数据损坏，json-validate 必须 FAIL 且两种模式输出一致

- changed: `demo/data/users.json`
- mode: `incremental`, affected: `json-validate`
- verdict full/incr: FAIL/FAIL, compare: CONSISTENT
- logs: `results/fault-json.full.txt`, `results/fault-json.incr.txt`, `results/fault-json.compare.txt`
