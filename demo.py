"""渲染样例 + 百万点性能基准。运行: python3 demo.py"""

import math
import random
import resource
import time
import tracemalloc
from array import array

from textwave import render


def show(title, data, width=78, height=12, **kw):
    print(f"=== {title} ===")
    print(render(data, width=width, height=height, **kw))
    print()


def main():
    rng = random.Random(0)

    show("正弦波 (n=2000)",
         [math.sin(i * 0.05) for i in range(2000)])

    show("正弦 + 单个离群点 1e6（鲁棒范围 + 裁剪标记）",
         [math.sin(i * 0.05) if i != 1000 else 1e6 for i in range(2000)])

    show("全零序列中的单尖峰（退化范围回退，尖峰完整可见）",
         [100.0 if i == 5000 else 0.0 for i in range(10000)])

    show("常数序列 [2.5]*500", [2.5] * 500)

    show("单点 [3.14]", [3.14])

    show("随机游走 (n=5000)",
         list(_accumulate(rng, 5000)))

    # ---- 百万点基准 ----
    n = 1_000_000
    data = array("d", (math.sin(i * 0.001) + rng.uniform(-0.2, 0.2)
                       for i in range(n)))
    # 耗时：tracemalloc 关闭，取 5 次中位数（避免内存追踪开销干扰）
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        out = render(data, width=120, height=24)
        times.append(time.perf_counter() - t0)
    elapsed = sorted(times)[len(times) // 2]
    # 内存：单独跑一次，tracemalloc 统计渲染过程中的 Python 堆分配
    tracemalloc.start()
    render(data, width=120, height=24)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"=== 基准: n={n}, width=120, height=24 ===")
    print(out.splitlines()[0])
    print(f"渲染耗时(5次中位): {elapsed*1000:.1f} ms")
    print(f"渲染峰值内存(tracemalloc): {peak/1024:.0f} KiB "
          f"(不含输入数据本身)")
    print(f"输入数据内存(array('d')): {data.itemsize*n/1024/1024:.1f} MiB")
    print(f"进程峰值 RSS: "
          f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024:.1f} MiB")


def _accumulate(rng, n):
    v = 0.0
    for _ in range(n):
        v += rng.uniform(-1, 1)
        yield v


if __name__ == "__main__":
    main()
