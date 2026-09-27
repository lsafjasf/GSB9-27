"""稳定性验证运行器（仅标准库）。

用法：
  python3 stability_runner.py              # 全部验证
  python3 stability_runner.py --rounds 30  # 指定乱序轮数

验证内容：
  A. 复现：缺陷套件在随机顺序下失败集合随顺序变化
  B. 顺序无关：修复套件乱序重复 N 轮，结果完全一致且全绿
  C. 并发一致：修复套件串行 vs 多线程并发，结果完全一致
"""
import argparse
import random
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

import repro_buggy_suite
import test_fixed_suite

ENV_KEY = "GSB_FIXTURE_PROFILE"


def iter_tests(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from iter_tests(t)
        else:
            yield t


def load(case_cls):
    return list(iter_tests(unittest.defaultTestLoader.loadTestsFromTestCase(case_cls)))


def outcome_of(test, result):
    tid = test.id()
    if any(t.id() == tid for t, _ in result.errors):
        return "error"
    if any(t.id() == tid for t, _ in result.failures):
        return "fail"
    if any(t.id() == tid for t, _ in result.skipped):
        return "skip"
    return "pass"


def run_serial(tests):
    result = unittest.TestResult()
    unittest.TestSuite(tests).run(result)
    return {t.id(): outcome_of(t, result) for t in tests}


def run_concurrent(tests, workers):
    outcomes = {}
    lock = threading.Lock()

    def run_one(t):
        result = unittest.TestResult()
        t.run(result)
        with lock:
            outcomes[t.id()] = outcome_of(t, result)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(run_one, tests))
    return outcomes


def short(tid):
    return tid.rsplit(".", 1)[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20260928)
    args = ap.parse_args()
    os_environ_clean()

    ok = True

    print("=" * 64)
    print("A. 复现：缺陷套件 fixture_buggy + repro_buggy_suite")
    print("=" * 64)
    repro_outcomes = []
    for r in range(6):
        tests = load(repro_buggy_suite.OrderDependentSuite)
        random.Random(args.seed + r).shuffle(tests)
        outcomes = run_serial(tests)
        repro_outcomes.append(outcomes)
        bad = sorted(short(t) for t, s in outcomes.items() if s != "pass")
        print("  轮次 %d 非通过用例(%d): %s" % (r, len(bad), ", ".join(bad)))
    distinct = len({tuple(sorted(o.items())) for o in repro_outcomes})
    print("  => %d 轮产生了 %d 种不同的结果分布（>1 即证明顺序相关）" % (len(repro_outcomes), distinct))
    if distinct < 2:
        print("  !! 未能复现顺序相关失败")
        ok = False

    print()
    print("=" * 64)
    print("B. 顺序无关性：修复套件乱序重复 %d 轮" % args.rounds)
    print("=" * 64)
    os_environ_clean()  # 清除复现段泄漏的环境变量，隔离段间影响
    baseline = None
    stable = True
    for r in range(args.rounds):
        tests = load(test_fixed_suite.OrderIndependentSuite)
        random.Random(args.seed + 1000 + r).shuffle(tests)
        outcomes = run_serial(tests)
        if baseline is None:
            baseline = outcomes
        if outcomes != baseline:
            stable = False
            print("  轮次 %d 结果与基准不一致！" % r)
    all_pass = all(s == "pass" for s in baseline.values())
    print("  轮数: %d, 每轮用例数: %d" % (args.rounds, len(baseline)))
    print("  各轮结果完全一致: %s" % stable)
    print("  全部通过: %s (pass=%d)" % (all_pass, sum(1 for s in baseline.values() if s == "pass")))
    if not (stable and all_pass):
        ok = False

    print()
    print("=" * 64)
    print("C. 并发一致性：串行 vs 并发(workers=%d)，各 10 轮" % args.workers)
    print("=" * 64)
    os_environ_clean()
    serial_ref = run_serial(load(test_fixed_suite.OrderIndependentSuite))
    consistent = True
    for r in range(10):
        tests = load(test_fixed_suite.OrderIndependentSuite)
        conc = run_concurrent(tests, args.workers)
        if conc != serial_ref:
            consistent = False
            diff = {t: (serial_ref.get(t), conc.get(t)) for t in conc if conc.get(t) != serial_ref.get(t)}
            print("  并发轮次 %d 与串行结果不一致: %s" % (r, diff))
    print("  串行结果: 全部 %s" % set(serial_ref.values()))
    print("  并发 10 轮结果与串行完全一致: %s" % consistent)
    if not consistent:
        ok = False

    print()
    print("总结: %s" % ("全部验证通过" if ok else "存在失败项"))
    return 0 if ok else 1


def os_environ_clean():
    import os
    os.environ.pop(ENV_KEY, None)


if __name__ == "__main__":
    sys.exit(main())
