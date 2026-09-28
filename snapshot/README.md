# 分块压缩快照库（snapshotstore）

仅使用 Python 3 标准库（`zlib` / `hashlib` / `struct` / `json`）。快照按固定大小
切块、逐块 zlib 压缩，并为每块保存偏移、压缩后大小、解压后大小和 SHA-256；
恢复时只解压需要的块，损坏块可被定位并从副本修复。

## 容器格式

```
+---------+----------------+-------+---------+-------------------+
| header  | chunk 0 (zlib) |  ...  | index   | trailer (48B)     |
| 16 B    | 变长            |       | JSON    | idx_off|idx_len|sha|
+---------+----------------+-------+---------+-------------------+
```

- header：`SNAPSHOT` 魔数 + 版本号（`>8sHH4x`，16 字节）。
- 每块是一个独立 zlib 流，可单独 seek 读取、单独解压，互不依赖。
- index：UTF-8 JSON，`chunks[]` 每项含 `offset`、`compressed_size`、
  `size`（解压后大小）、`sha256`（**解压后**字节的 SHA-256）。
- trailer：索引的起始偏移、长度、索引本身的 SHA-256（`>QQ32s`，48 字节），
  位于文件末尾，从尾部一次读取即可定位索引，无需扫描全文件。

## 核心 API（`snapshotstore.py`）

- `write_snapshot(path, source, chunk_size=64KiB, level=-1)`：bytes 或文件流输入，
  先写 `.tmp` 再 `os.replace` 原子落盘，返回统计字典。
- `SnapshotReader(path)`：打开即校验头、尾、索引摘要及块偏移连续性。
  - `read_chunk(i)`：只解压并校验第 `i` 块；校验失败抛 `CorruptChunkError`
    （`.index` 给出块号，`.reason` 给出原因），绝不返回错误数据。
  - `read_range(start, length=.. / end=..)`：随机读解压空间的任意字节区间，
    只解压覆盖该区间的少数块，结果按块边界拼接。
  - `read_all()`：解压并校验全部块（往返无损断言的基础）。
  - `verify()`：逐块校验，返回所有损坏块的下标；健康快照返回 `[]`。
  - `locate(offset)`：返回某解压偏移所在的块号。
- `restore_chunks(target, replica_paths, indices=None, auto_repair=False)`：
  用一个或多个副本修复损坏块。逐副本尝试，按块的解压后大小 + SHA-256 匹配，
  副本自身损坏或内容不一致会跳过；没有任何健康副本时抛
  `MissingReplicaChunkError` 且目标文件不变。修复走临时文件 + 原子替换，
  允许副本使用不同压缩级别（重建时重算偏移与索引）。

## 运行命令

```bash
cd snapshot
python3 -m unittest -v test_snapshotstore   # 全部自测（21 个用例）
python3 demo.py                             # 写入/随机读/损坏定位/修复 演示
python3 benchmark.py                       # 压缩率 + 随机读耗时（默认 16MiB）
python3 benchmark.py 64 64                  # 参数：快照 MiB、块 KiB
```

## 测试覆盖（`test_snapshotstore.py`）

- 空快照、单块（小于/恰为一块）、块边界未对齐、流式输入。
- **上千块**：1201 块（1KiB 块）整体逐字节往返。
- 随机读：200 个随机区间与原始切片逐一比对；跨块、整段边界、越界拒绝。
- 损坏定位：翻转中间块/末块/多块字节，`verify()` 精确报块号；
  `read_chunk` / `read_range` / `read_all` 均拒绝返回错误数据，邻近好块仍可读。
- 索引损坏、文件截断、魔数错误：打开即抛 `CorruptSnapshotError`。
- 副本恢复：正常修复、不同压缩级别副本修复、多副本自动跳过坏副本挑健康副本、
  全部副本都坏时抛异常且不改原文件、内容不同的副本被拒绝。

## 基准数据（本机，Python 3.12，Linux x86_64）

`python3 benchmark.py`（负载为 75% 高重复结构化数据 + 25% 随机段，
模拟状态快照；每次随机读均与原始字节断言一致）：

```
Case A: 16 MiB 快照，64 KiB 块（256 块）
  原始 16,777,216 B -> 压缩 3,763,567 B   压缩率 0.224（节省 77.6%）
  写入+压缩            105.28 ms
  全量解压（对照基线）   37.55 ms
  5000 次随机读        513.58 ms  合计，平均 102.72 us/次
  损坏定位（全量校验）  chunk [128]，24.47 ms
  副本修复（原子重写）  chunk [128]，30.35 ms

Case B: 4 MiB 快照，2 KiB 块（2048 块，1000+ 块场景）
  原始 4,194,304 B -> 压缩 1,356,994 B   压缩率 0.324（节省 67.6%）
  写入+压缩             55.98 ms
  全量解压              10.81 ms
  2000 次随机读         20.83 ms  合计，平均 10.41 us/次
  损坏定位              chunk [1024]，8.13 ms
  副本修复              chunk [1024]，15.42 ms
```

解读：随机读只触及 1–2 个小块，Case B 平均约 10 us/次，远低于全量解压
10.81 ms；块越小随机读越省，但每块固定的 zlib 头/尾会降低压缩率（32.4% vs
22.4%）。校验值覆盖解压后数据，因此压缩流损坏、解压长度不符、内容被替换都会
被检出。
