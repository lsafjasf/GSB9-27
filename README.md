# 键比较修复（字典查找 / 去重）

旧实现仅做 `lower()`，全角半角、组合字符、变音符号变体被错误判为不同。
修复改为对确定性的规范化形式比较，区域可显式选择（默认 `root`，可选 `tr`）。
仅使用 Python 3 标准库（`unicodedata`）。

## 文件

- `keycmp/normalizer.py`：修复后的规范化与比较实现
- `tests/reproduce_bug.py`：修复前问题的稳定复现脚本（自包含，不依赖修复代码）
- `tests/test_keycmp.py`：回归测试（等价类断言、自反/对称/传递、区域差异、查找/去重冒烟）
- `migrate_index.py`：只读的索引冲突扫描与旧键→新键映射生成工具
- `MIGRATION.md`：等价类变化说明与迁移/重建索引步骤

## 运行命令

```bash
# 1) 复现旧实现的问题（退出码 0 = 问题已复现）
python3 tests/reproduce_bug.py

# 2) 跑回归 + 等价类断言测试
python3 -m unittest tests.test_keycmp -v

# 3) 扫描旧索引中会合并的键（退出码 2 = 存在需人工处理的冲突）
python3 migrate_index.py --keys old_keys.txt --locale root \
    --report collision_report.txt --map key_remap.json
```

## 用法

```python
from keycmp import normalize_key, keys_equal, KeyNormalizer

normalize_key("ＣＡＦÉ") == normalize_key("café") == "café"      # True（规范化等价）
keys_equal("Straße", "STRASSE")                      # True (root, casefold ß->ss)
keys_equal("Işık", "ışık", locale="tr")              # True（土耳其语 I->ı）
keys_equal("Işık", "ışık", locale="root")            # False
keys_equal("café", "cafe")                           # False（默认不去音符）
keys_equal("café", "cafe", strip_marks="latin")      # True（显式开启）
keys_equal("が", "か")                                # False（浊点不会被误删）

norm = KeyNormalizer(locale="tr")
table = {norm.normalize(k): v for k, v in raw_items.items()}
table[norm.normalize("İSTANBUL")]                     # 查找任意等价写法
```

## 去记号（strip_marks）按语言可配置

题面点名的现象（大小写、全角/半角、预组合 vs 组合写法）默认即处理；
“删变音记号”是显式可选项，避免把不同文字错误合并：

| `strip_marks` | 行为 | 例子 |
|---|---|---|
| `none`（默认） | 不删记号，只做规范化等价 | `é`==`e+◌́`，但 `café`!=`cafe`、`が`!=`か` |
| `latin` | 只删拉丁基字母上的组合记号 | `café`==`cafe`、`İ`==`i`(root)；`が`/`й`/`ά` 仍各自独立 |
| `all` | 删除全部 Mn（旧行为，仅兼容用） | `が`==`か`（⚠️ 跨文字合并） |

## 默认规则 vs 可选规则（locale）

| | `root`（默认，Unicode 无区域） | `tr`（土耳其语，显式可选） |
|---|---|---|
| 全角/半角、组合字符 | 统一 | 统一 |
| `ß` 折叠 | `ß`→`ss` | 同 root |
| 大写 `I` | →`i`（`I` 与 `i` 同键） | →`ı`（U+0131，`I` 与无点 ı 同键，与 `i` 不同） |
| `İ`(U+0130) | casefold 为 `i`+附加点（默认保留，与 `i` 不同） | 显式 →`i` |

切换区域会改变等价类，已建索引必须按 `MIGRATION.md` 重建。
