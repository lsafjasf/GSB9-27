# 分块传输解码（HTTP chunked transfer coding, RFC 9112 §7.1）

流式分块解码器，仅使用 Python 3 标准库。网络数据可在任意字节位置切分喂入，
同一份编码流无论怎么切分，产出的还原数据逐字节一致。

## 文件

| 文件 | 内容 |
| --- | --- |
| `chunked_decoder.py` | 库：`ChunkedDecoder`、`ChunkedDecodeError`、`encode_chunked()` |
| `test_chunked_decoder.py` | 自测（32 例）：等价性对拍、边界情形、错误定位 |
| `bench_chunked.py` | 吞吐基准（默认 64 MiB 载荷） |

## 用法

```python
from chunked_decoder import ChunkedDecoder

dec = ChunkedDecoder(max_chunk_size=64 * 1024 * 1024)
payload = b""
while True:
    piece = sock.recv(65536)
    if not piece:
        break
    payload += dec.feed(piece)
payload += dec.finish()           # EOF 时校验结尾零块，未完成则抛错
print(dec.trailers)               # [(b"X-Checksum", b"..."), ...]
print(dec.chunks)                 # [(size, ((name, value), ...)), ...]
```

状态机：读块大小行（含扩展参数）→ 读块数据 → 校验 CRLF → 循环；
读到 `0` 块后进入尾随头部（trailer）解析，空行结束。
扩展参数支持 `name`、`name=token`、`name="quoted string"`（含反斜杠转义）。

## 运行命令

```bash
python3 test_chunked_decoder.py -v   # 全部自测
python3 test_chunked_decoder.py      # 末尾打印错误定位样例表
python3 bench_chunked.py             # 吞吐基准
```

## 切分等价性对拍（`EquivalenceTest`）

- **二分穷举**：在同一编码流的每个字节切点（0..N）切成两段分别喂入，共 N+1 种。
- **三分穷举**：穷举所有三段切分方式。
- **逐字节喂入**：每次只喂 1 字节。
- **随机性质测试（50 组 × 10 种切分）**：随机载荷（0–5000 字节）、随机块边界、
  随机扩展参数与尾随头部，随机 1–30 个切点，全部与"一次性喂入"的参考输出逐字节比对。

## 覆盖的情形

- 空消息（仅 `0\r\n\r\n`）、零块带扩展参数、尾随头部。
- 超长块：8 MiB 大块正常解码；声明大小超过 `max_chunk_size` 立即报错。
- 单字节块：256 个连续 `1\r\nX\r\n` 块。
- 连续多块一次到达（~1.4 万块一次 feed）。

## 非法输入与错误定位

所有错误均抛 `ChunkedDecodeError`，带绝对字节偏移 `offset`（在编码流中的位置）。

自测打印的样例（`python3 test_chunked_decoder.py`）：

| 错误类型 | 输入 | 偏移 | 信息 |
| --- | --- | --- | --- |
| 长度非法 | `1Z\r\n` | 1 | invalid character 'Z' in chunk size |
| 缺少结束块 | `3\r\nabc\r\n` | 8 | missing terminal zero-size chunk |
| 块大小与数据不符（EOF） | `5\r\nabc` | 6 | stream ended inside chunk data: declared 5 bytes, received 3 |
| 块大小与数据不符（CRLF 错位） | `3\r\nabcXX` | 6 | expected CRLF after chunk data, found b'XX' |
| 扩展参数错误（杂散字符） | `3;foo bar\r\n` | 6 | unexpected character 'b' in chunk extensions, expected ';' |

其余样例见 `ErrorTest`：空长度、大小行过长、扩展缺名/缺值、引号串未闭合或含
非法字符、trailer 无冒号/字段名非法、trailer 区内 EOF、结束块之后的多余数据。

## 吞吐数据

环境：Python 3.12.3，Linux x86-64，64 MiB 随机载荷；MB/s 按还原后载荷字节数计，
每种配置 3 轮取最佳。

| 编码块大小 | feed 切分 | 吞吐 (MB/s) |
| --- | --- | ---: |
| 64 KiB | 64 KiB | ~11500 |
| 64 KiB | 4 KiB | ~4000 |
| 64 KiB | 1 MiB | ~1400 |
| 64 KiB | 整份一次喂入 | ~930 |
| 8 KiB | 64 KiB | ~3000 |
| 8 KiB | 4 KiB | ~2000 |
| 8 KiB | 1 MiB | ~2500 |
| 8 KiB | 整份一次喂入 | ~720 |
| 1 KiB | 64 KiB / 1 MiB | ~930 |
| 1 KiB | 4 KiB | ~770 |
| 1 KiB | 整份一次喂入 | ~470 |
| 64 KiB | 每次 1 字节（4 MiB 流，病态切分） | ~2.5 |

说明：小块编码的瓶颈是每块多次 Python 级状态机循环；大 feed + 大缓冲区受
冷内存带宽影响；逐字节喂入（每字节一次 `feed()` 调用）属病态情形，仅作
正确性验证参考。正常网络收包粒度（4–64 KiB）下吞吐为 2–11 GB/s。
