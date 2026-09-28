# GSB9-27

版本化数据格式升级修复。详见 [MIGRATION.md](MIGRATION.md)。

- `repro_bug.py` — 复现旧版升级逻辑静默丢字段的用例
- `dataformat/` — 修复版迁移引擎（纯标准库）
- `tests/` — 回归与兼容性测试
- `migrate_tool.py` — 命令行迁移/降级/往返工具，输出逐字段迁移报告
- `verify_roundtrip.py` — 可复跑的端到端验证（报告核对 + 回退 + 往返差异）

```bash
python3 repro_bug.py
python3 -m unittest discover -s tests -v
python3 verify_roundtrip.py
```
