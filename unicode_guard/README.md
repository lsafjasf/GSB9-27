# unicode_guard

黑名单/用户名校验的 Unicode 归一化与同形字符防御库（Python 3，仅标准库）。

## 文件

- `unicode_guard.py` — 核心库：归一化、同形骨架、不可见字符检测、策略裁决
- `bypass_cases.py` — 绕过用例集（16 例）+ 检出结果
- `test_unicode_guard.py` — 等价类断言与策略行为自测（17 个测试）
- `demo_blacklist.py` — 黑名单/占用名校验端到端演示
- `STRATEGY.md` — 比较/存储/展示三环节形式约定与策略说明

## 运行

```bash
cd unicode_guard
python3 -m unittest test_unicode_guard -v   # 等价类断言 + 策略自测
python3 bypass_cases.py                     # 绕过用例集检出结果
python3 demo_blacklist.py                   # 黑名单校验演示
```

## 快速上手

```python
from unicode_guard import Config, Policy, analyze, canonical_key

cfg = Config(invisible_policy=Policy.REJECT,   # 零宽字符：拒绝
             confusable_policy=Policy.WARN,     # 同形字符：告警
             mixed_script_policy=Policy.WARN)   # 混用文字系统：告警

report = analyze("аdm​in", cfg)
report.ok          # False（含不可见字符）
report.canonical   # 'admin' —— 与黑名单键比对用
report.storage     # 存储形式（剥不可见 + NFC）
report.display     # 展示形式（原样 NFC）

blocked = {canonical_key(x) for x in ["admin", "root"]}
if report.canonical in blocked:
    ...            # 拒绝：同形/大小写/零宽绕过全部命中同一键
```
