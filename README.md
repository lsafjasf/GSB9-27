# GSB9-27 数据格式升级修复

分版本数据格式（v1/v2/v3）的迁移逻辑，仅依赖 Python 3 标准库。

## 文件

- `src/legacy_upgrade.py` — 旧版有缺陷的升级逻辑（仅用于复现）
- `src/dataformat.py` — 修复后的迁移实现（唯一权威源码）
- `repro_bug.py` — 复现脚本：稳定复现字段丢失/默认值掩盖
- `repro_tests.py` — 三类原缺陷的*行为契约*（与引擎无关的 unittest 用例）
- `run_repro.py` — 同一份契约分别在旧引擎（预期红）/新引擎（预期绿）上执行，提供红绿实证
- `test_upgrade.py` — 回归测试 + 兼容性测试（unittest，含修复引擎上的契约）
- `MIGRATION.md` — 逐版本字段对照表与降级行为说明

## 运行

```bash
# 复现旧逻辑的字段丢失（退出码 0 表示复现成功）
python3 repro_bug.py

# 运行全部回归与兼容性测试（含修复引擎上的复现契约，共 27 条）
python3 -m unittest test_upgrade -v

# 红绿实证：同一份用例在旧引擎必须全红、在修复引擎必须全绿
python3 run_repro.py both
```
