# GSB9-27 数据格式升级修复

分版本数据格式（v1/v2/v3）的迁移逻辑，仅依赖 Python 3 标准库。

## 文件

- `src/legacy_upgrade.py` — 旧版有缺陷的升级逻辑（仅用于复现）
- `src/dataformat.py` — 修复后的迁移实现（唯一权威源码）
- `repro_bug.py` — 复现脚本：稳定复现字段丢失/默认值掩盖
- `test_repro.py` — 复现用例：同一组断言分别打在旧逻辑（预期失败）与新引擎（预期全绿）上
- `test_upgrade.py` — 回归测试 + 兼容性测试（unittest）
- `MIGRATION.md` — 逐版本字段对照表与降级行为说明

## 运行

```bash
# 复现旧逻辑的字段丢失（退出码 0 表示复现成功）
python3 repro_bug.py

# 运行全部回归与兼容性测试
python3 -m unittest test_upgrade -v

# 实证复现用例能抓住原缺陷：旧逻辑下应失败，新引擎下应全绿
python3 -m unittest test_repro.TestReproOnLegacy -v   # 预期 FAILED
python3 -m unittest test_repro.TestReproOnFixed -v    # 预期 OK
```
