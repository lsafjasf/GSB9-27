# 键比较修复（字典查找 / 去重）

旧实现仅做 `lower()`，全角半角、组合字符与预组合字符的编码变体被错误判为不同。
修复改为对确定性的规范化形式比较，区域可显式选择（默认 `root`，可选 `tr`）。
**去记号（去变音符号）默认关闭**，可按语言显式开启，避免把日文浊音、拉丁
变音符号这类“肉眼不同”的键错误合并。仅使用 Python 3 标准库（`unicodedata`）。

## 文件

- `keycmp/normalizer.py`：修复后的规范化与比较实现
- `tests/reproduce_bug.py`：修复前问题的稳定复现脚本（自包含，不依赖修复代码）
- `tests/test_keycmp.py`：回归测试（等价类断言、自反/对称/传递、区域差异、跨文字不合并、查找/去重冒烟）
- `migrate_index.py`：只读的索引冲突扫描与旧键→新键映射生成工具
- `MIGRATION.md`：等价类变化说明与迁移/重建索引步骤

## 运行命令

```bash
# 1) 复现旧实现的问题（退出码 0 = 问题已复现）
python3 tests/reproduce_bug.py

# 2) 跑回归 + 等价类 + 跨文字断言测试
python3 -m unittest tests.test_keycmp -v

# 3) 扫描旧索引中会合并的键（退出码 2 = 存在需人工处理的冲突）
python3 migrate_index.py --keys old_keys.txt --locale root \
    --report collision_report.txt --map key_remap.json
```

## 用法

```python
from keycmp import normalize_key, keys_equal, KeyNormalizer

# 默认：统一编码变体，但保留所有记号（が≠か、café≠cafe）
normalize_key("ＣＡＦÉ") == normalize_key("cafe\u0301") == "caf\u00e9"  # True
keys_equal("Straße", "STRASSE")                       # True (root, casefold ß->ss)
keys_equal("が", "か")                                 # False（浊音符保留）
keys_equal("が", "か\u3099")                           # True （预组合 == 组合写法）
keys_equal("caf\u00e9", "cafe")                       # False（默认不去音符）
keys_equal("Işık", "ışık", locale="tr")               # True（土耳其语 I->ı）
keys_equal("Işık", "ışık", locale="root")             # False

# 需要 accent-insensitive 时按语言显式开启：只剥拉丁基字符上的记号
norm = KeyNormalizer(strip_marks="latin")
norm.normalize("caf\u00e9") == "cafe"                 # True
norm.equal("が", "か")                                 # False（假名浊音符不受影响）

norm = KeyNormalizer(locale="tr", strip_marks="latin")
table = {norm.normalize(k): v for k, v in raw_items.items()}
table[norm.normalize("İSTANBUL")]                     # 查找任意等价写法
```

## 默认规则 vs 可选规则

| | `root`（默认，Unicode 无区域） | `tr`（土耳其语，显式可选） |
|---|---|---|
| 全角/半角、合字、组合/预组合字符 | 统一（NFKC + NFC） | 统一 |
| 去变音记号（`café` vs `cafe`） | **默认不合并**；`strip_marks="latin"` 时仅拉丁合并 | 同 root（按 `strip_marks`） |
| 日文浊音/半浊音（`が` vs `か`） | 永不合并（含 `strip_marks="latin"`） | 永不合并 |
| `ß` 折叠 | `ß`→`ss` | 同 root |
| 大写 `I` | →`i`（`I` 与 `i` 同键） | →`ı`（U+0131，`I` 与无点 ı 同键，与 `i` 不同） |
| `İ`(U+0130) | 保留组合点，与 `i` 不同 | 显式 →`i` |

## 去记号策略 `strip_marks`

去记号只删除**挂在已开启文字系统的基字符后面**的组合记号（Unicode 类别 Mn）：

- `None`（默认）：不删除任何记号。组合序列经 NFC 与预组合形式统一，
  但 `café` ≠ `cafe`、`が` ≠ `か`。
- `"latin"`：仅剥离拉丁基字符上的记号（`café`→`cafe`、`über`→`uber`）；
  假名的浊音符 U+3099 / 半浊音符 U+309A 永远保留。
- `"all"`：旧的无条件删除行为，**不推荐**——会把 `が`/`か`、`ぱ`/`は`
  合并；仅为需要旧行为的迁移场景显式保留。
- 自定义：传入脚本名可迭代，如 `{"latin", "greek"}`。

切换区域或去记号策略都会改变等价类，已建索引必须按 `MIGRATION.md` 重建。
