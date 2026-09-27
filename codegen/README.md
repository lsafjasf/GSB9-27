# codegen — 结构化代码骨架生成器（Python 3，仅标准库）

从配置确定性地生成目标语言（Python）代码骨架。缩进由作用域结构决定，
调用方不手写空格；同一配置多次生成逐字节一致；生成结果经 `ast.parse`
解析验证。

## 文件

- `codegen.py` — 核心库：`CodeBuilder`（作用域块 / 条件 / 循环 / 注释 / 空行）、`safe_identifier`
- `example_generator.py` — 配置（JSON）驱动的示例生成器 + CLI
- `test_codegen.py` — 21 个自测（unittest）
- `bench.py` — 大配置性能基准

## 运行命令

```bash
cd codegen
python3 -m unittest test_codegen -v     # 运行全部自测
python3 example_generator.py            # 内置演示配置，输出到 stdout
python3 example_generator.py cfg.json -o out.py   # 从 JSON 配置生成文件（写出前自动 ast.parse）
python3 bench.py 20                     # 性能基准（20 轮）
```

## 缩进处理说明

- 缩进层级完全由 `with b.block(header):` 的嵌套深度决定，库内部维护
  `_depth`，每行渲染为 `indent_unit * depth + text`；调用方只写逻辑行，
  不允许也无法手写前导空格。
- **空块省略**：块退出时检查块体是否没有任何非空行；为空则按 `on_empty`
  策略处理——`"omit"`（默认）整块删除（块头+块体，不留空行或悬挂缩进）、
  `"pass"` 补占位语句（保证语法合法）、`"keep"` 原样保留。内层块先省略，
  外层的空检测自然级联，层层为空的结构会整体消失。
- **条件生成**：`block(..., when=False)` 进入抑制模式，块头与块体全部丢弃；
  `b.when(cond, fn)` 是单行等价物。
- **循环生成**：`b.for_each(items, fn)`；传入 set 时先按 `repr` 排序，
  与哈希随机化无关。
- **渲染归一化**（均为确定性操作）：去掉首尾空白行；连续空行折叠到最多
  2 行；文件以恰好一个 `\n` 结尾。
- **关键字冲突**：`safe_identifier` 把任意配置字符串确定性地映射为合法
  标识符——非法字符替换为 `_`，数字开头或空串前缀 `_`，与硬/软关键字
  冲突后缀 `_`（如 `class` → `class_`、`match` → `match_`）。

## 确定性保证

- 不依赖时间戳、对象 id、`PYTHONHASHSEED`；set 迭代前排序，dict 按配置
  中的插入顺序遍历。
- 测试 `test_same_config_same_bytes_across_processes_and_hash_seeds` 在
  `PYTHONHASHSEED=0/1/42` 三个子进程中生成并逐字节比对。
- `bench.py` 每轮断言输出与首轮逐字节一致。

## 性能数据（bench.py，本机 Python 3.12.3）

配置规模：300 类 × 15 方法 × 9 语句 + 200 函数 + 100 常量，
生成 56,807 行 / 917.8 KiB：

| 指标 | 数值 |
|---|---|
| 生成耗时（20 轮） | min 17.8 ms / median 18.6 ms / max 29.6 ms |
| 吞吐 | ≈ 3,059 k行/s（48.3 MiB/s） |
| ast.parse 校验 | 264.2 ms（可选步骤，不计入生成耗时） |

复现：`python3 bench.py 20`
