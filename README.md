# GSB9-27 流式 NDJSON 解析器

Python 3（仅标准库）。把"一次性拿到完整输入"的解析器改造为可分块喂入、
逐个产出记录的流式解析器，内部缓冲严格有界。

## 记录格式

NDJSON：每条记录是一行 UTF-8 JSON 值，以 `\n` 分隔（兼容 `\r\n`）；
空行跳过且不计入记录序号。

## API（stream_parser.py）

- `StreamingParser(max_record_size=1<<20)`
  - `feed(chunk) -> list`：喂入一块字节，返回本次完整解析出的记录；空块是合法 no-op。
  - `finish()`：正常结束；缓冲中残留不完整记录时抛 `TruncatedInputError`（报错，不丢弃）。
  - `abort()`：上游中断提前结束；残留数据直接丢弃，不报错。
  - 只读状态：`records_emitted` / `bytes_consumed` / `buffered_bytes`。
- `parse_all(data)`：一次性解析参考实现，用于等价性对拍。

## 缓冲上界与残留处置

- 内部缓冲只保存"未见到换行符的不完整记录"，长度**始终 ≤ max_record_size**（默认 1 MiB）。
- 单条记录超限立即抛 `RecordTooLargeError`，缓冲拒绝继续增长（不会为超大记录扩容）。
- 残留数据只在两处终结：`finish()` 报错 / `abort()` 丢弃；解析错误抛出后实例应被丢弃。

## 错误定位

所有 `ParserError` 子类（`ParseError` / `RecordTooLargeError` / `TruncatedInputError`）携带：

- `record_index`：出错记录是第几条（0 起，即出错前已成功产出的记录数）；
- `line_no`：出错记录所在的行号（1 起，空行也计数）；
- `byte_offset`：**出错字节本身**在整个输入流中的绝对偏移（0 起）。
  JSON 错误按 `JSONDecodeError.pos` 把字符位置换算成 UTF-8 字节偏移，
  编码错误按 `UnicodeDecodeError.start` 定位到首个非法字节；
  记录级错误（超限 / 截断）定位到该记录的首字节。

## 运行命令

```bash
python3 -m unittest test_stream_parser -v   # 17 个自测（等价性/边界/错误定位/内存上界）
python3 bench.py                            # 内存峰值与吞吐基准
```

## 测试覆盖

- 分块等价性：块大小 1..256 全枚举 + 200 组随机分块（含空块），与 `parse_all` 逐条相同；
- 记录跨多个块（逐字节喂入 10 KB 记录）、单条记录超缓冲上限（报错且缓冲仍有界）、
  空块、末尾不完整记录（`finish` 报 `TruncatedInputError`，已产出记录不丢）、
  上游中断（`abort` 丢弃残留）、错误定位（第几条 / 第几行 / 出错字节精确偏移，
  含多字节字符前的字节换算）、CRLF 与空行。

## 实测数据（Python 3.12，20 万条记录 / 52 MB 输入）

| 模式 | 内存峰值 | 耗时 | 吞吐 |
|---|---|---|---|
| 一次性 parse_all | 159.82 MB | 2.91 s | 17.8 MB/s |
| 流式 4 KiB 块 | 0.02 MB | 2.12 s | 24.5 MB/s |
| 流式 64 KiB 块 | 0.27 MB | 2.16 s | 24.1 MB/s |
| 流式 1 MiB 块 | 4.28 MB | 2.36 s | 22.0 MB/s |
| 流式 64 KiB 块（结果全留存） | 159.84 MB | 2.98 s | 17.5 MB/s |

结论：下游即时消费时流式峰值与输入总量无关（9 MB 输入下测试断言峰值 < 1 MB）；
最后一行说明若调用方把结果全部留存，内存由结果列表主导，与解析器无关。
