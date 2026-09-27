"""大配置生成耗时基准。

运行：python3 bench.py [轮数]
"""

from __future__ import annotations

import ast
import statistics
import sys
import time

from example_generator import generate


def big_config(n_classes: int = 300, n_methods: int = 15, n_stmts: int = 8) -> dict:
    return {
        "module_doc": "基准大配置",
        "imports": ["json", "os", "sys"],
        "from_imports": {"typing": ["Any", "Optional"]},
        "constants": {f"CONST_{i}": i for i in range(100)},
        "classes": [
            {
                "name": f"Service{i}",
                "bases": ["object"],
                "docstring": f"服务 {i}",
                "attributes": {f"attr_{j}": j for j in range(5)},
                "methods": [
                    {
                        "name": f"method_{j}",
                        "args": ["self", f"arg_{j}"],
                        "comment": f"方法 {j}",
                        "body": [f"x_{k} = {k}" for k in range(n_stmts)]
                                + [f"return x_{n_stmts - 1}"],
                    }
                    for j in range(n_methods)
                ],
            }
            for i in range(n_classes)
        ],
        "functions": [
            {"name": f"helper_{i}", "args": ["x"], "body": ["return x"]}
            for i in range(200)
        ],
    }


def main() -> None:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    config = big_config()

    source = generate(config)  # 预热 + 统计规模
    n_lines = source.count("\n")
    n_bytes = len(source.encode("utf-8"))

    times = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        out = generate(config)
        times.append(time.perf_counter() - t0)
        assert out == source  # 每轮逐字节一致

    t0 = time.perf_counter()
    ast.parse(source)
    parse_ms = (time.perf_counter() - t0) * 1000

    med = statistics.median(times)
    print(f"配置规模      : 300 类 x 15 方法 x 9 语句 + 200 函数 + 100 常量")
    print(f"生成规模      : {n_lines} 行 / {n_bytes / 1024:.1f} KiB")
    print(f"轮数          : {rounds}（每轮结果逐字节一致）")
    print(f"生成耗时      : min {min(times)*1000:.1f} ms / "
          f"median {med*1000:.1f} ms / max {max(times)*1000:.1f} ms")
    print(f"吞吐          : {n_lines / med / 1000:.0f} k行/s "
          f"({n_bytes / med / 1024 / 1024:.1f} MiB/s)")
    print(f"ast.parse 校验: {parse_ms:.1f} ms")
    print(f"Python        : {sys.version.split()[0]}")


if __name__ == "__main__":
    main()
