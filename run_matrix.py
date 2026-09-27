"""稳定性验证：
  阶段 1（串行顺序无关性）：随机打乱用例顺序，重复 N 轮，逐轮比对结果签名。
  阶段 2（并发一致性）：线程池并发执行，重复 N 轮，与串行基线逐用例比对。

用法：
  python3 run_matrix.py                      # 验证修复版（默认 tests_fixed）
  python3 run_matrix.py --module tests_buggy # 复现 buggy 版的不稳定
  python3 run_matrix.py --rounds 30 --jobs 8
"""
import argparse
import concurrent.futures
import hashlib
import importlib
import random
import re
import subprocess
import sys
import unittest


def load_cases(module_name):
    module = importlib.import_module(module_name)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    cases = []

    def flatten(s):
        for item in s:
            if isinstance(item, unittest.TestSuite):
                flatten(item)
            else:
                cases.append((type(item), item._testMethodName))

    flatten(suite)
    return cases


def run_one(cls, method):
    case = cls(method)
    result = unittest.TestResult()
    case.run(result)
    if result.skipped:
        return "skipped"
    if result.errors:
        return "error"
    if result.failures:
        return "failed"
    return "passed"


def signature(outcomes):
    blob = "\n".join(f"{k}={v}" for k, v in sorted(outcomes.items()))
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def case_id(cls, method):
    return f"{cls.__name__}.{method}"


def serial_round(cases, seed):
    order = cases[:]
    random.Random(seed).shuffle(order)
    return {case_id(c, m): run_one(c, m) for c, m in order}


FAIL_RE = re.compile(r"^(?:FAIL|ERROR): \w+ \(([\w.]+)\)", re.M)


def serial_round_fresh(module_name, cases, seed):
    """在全新子进程中按打乱后的顺序执行，模拟独立 CI 运行。"""
    order = cases[:]
    random.Random(seed).shuffle(order)
    ids = [f"{module_name}.{c.__name__}.{m}" for c, m in order]
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "-q", *ids],
        capture_output=True, text=True,
    )
    failed = {".".join(m.group(1).split(".")[-2:]) for m in FAIL_RE.finditer(proc.stderr)}
    return {case_id(c, m): ("failed" if case_id(c, m) in failed else "passed")
            for c, m in cases}


def concurrent_round(cases, seed, jobs):
    order = cases[:]
    random.Random(seed).shuffle(order)
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(run_one, c, m): case_id(c, m) for c, m in order}
        return {futs[f]: f.result() for f in futs}


def summarize(outcomes):
    counts = {}
    for v in outcomes.values():
        counts[v] = counts.get(v, 0) + 1
    return ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", default="tests_fixed")
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--fresh", action="store_true",
                    help="串行阶段每轮在全新子进程中运行（模拟独立 CI 运行）")
    args = ap.parse_args()

    cases = load_cases(args.module)
    print(f"模块 {args.module}: {len(cases)} 个用例, {args.rounds} 轮, 并发度 {args.jobs}\n")

    print("== 阶段 1: 串行乱序 ==")
    serial_sigs = []
    baseline = None
    for r in range(args.rounds):
        if args.fresh:
            outcomes = serial_round_fresh(args.module, cases, seed=1000 + r)
        else:
            outcomes = serial_round(cases, seed=1000 + r)
        sig = signature(outcomes)
        serial_sigs.append(sig)
        if baseline is None:
            baseline = outcomes
        print(f"  round {r:02d}  sig={sig}  ({summarize(outcomes)})")
    serial_stable = len(set(serial_sigs)) == 1
    print(f"  => {'稳定：所有轮次签名一致' if serial_stable else '不稳定：签名不一致'}\n")

    print("== 阶段 2: 并发 vs 串行 ==")
    consistent = True
    for r in range(args.rounds):
        outcomes = concurrent_round(cases, seed=2000 + r, jobs=args.jobs)
        sig = signature(outcomes)
        match = outcomes == baseline
        consistent &= match
        print(f"  round {r:02d}  sig={sig}  与串行基线{'一致' if match else '不一致'}  ({summarize(outcomes)})")
    print(f"  => {'并发与串行结果完全一致' if consistent else '并发与串行结果不一致'}\n")

    ok = serial_stable and consistent
    print("结论:", "PASS —— 顺序无关且并发一致" if ok else "FAIL —— 存在用例间状态污染")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
