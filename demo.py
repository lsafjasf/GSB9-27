"""完成率对比 demo：四类典型失败结构，隔离前 vs 修复重试后。

运行：python3 demo.py
"""

from failure_isolation import Orchestrator


def ok(name):
    return lambda: f"done-{name}"


def boom():
    def fn():
        raise RuntimeError("task crashed")
    return fn


def scenario(title, build, fixes):
    o = Orchestrator()
    build(o)
    o.run()
    before = o.stats()
    print(o.report(f"{title} —— 隔离后（失败已发生，无关分支继续跑）"))
    o.resume(fixes=fixes)
    after = o.stats()
    print(o.report(f"{title} —— 局部重试后（只重跑受影响子图）"))
    print(f">>> 完成率: {before['completion_rate']:.0%} -> "
          f"{after['completion_rate']:.0%}  "
          f"(失败 {len(before['failed'])}, 跳过 {len(before['skipped'])}, "
          f"无关分支完成 {len(before['completed'])})\n")
    return before, after


def main():
    # 1) 单点失败
    def single(o):
        o.add_task("root", boom())
        o.add_task("child", ok("child"), deps=["root"])
        o.add_task("unrelated_a", ok("unrelated_a"))
        o.add_task("unrelated_b", ok("unrelated_b"))
    scenario("1. 单点失败", single, {"root": ok("root-fixed")})

    # 2) 扇出失败
    def fanout(o):
        o.add_task("a", ok("a"))
        o.add_task("b", boom(), deps=["a"])
        o.add_task("c", ok("c"), deps=["a"])
        o.add_task("d", ok("d"), deps=["b"])
        o.add_task("e", ok("e"), deps=["b"])
        o.add_task("f", ok("f"), deps=["c"])
        o.add_task("i", ok("i"))
    scenario("2. 扇出失败", fanout, {"b": ok("b-fixed")})

    # 3) 共享依赖失败
    def shared(o):
        o.add_task("shared", boom())
        o.add_task("x", ok("x"), deps=["shared"])
        o.add_task("y", ok("y"), deps=["shared"])
        o.add_task("join", ok("join"), deps=["x", "y"])
        o.add_task("other", ok("other"))
    scenario("3. 共享依赖失败", shared, {"shared": ok("shared-fixed")})

    # 4) 全部失败
    def total(o):
        o.add_task("r1", boom())
        o.add_task("r2", boom())
        o.add_task("m", ok("m"), deps=["r1", "r2"])
        o.add_task("leaf", ok("leaf"), deps=["m"])
    scenario("4. 全部失败", total,
             {"r1": ok("r1-fixed"), "r2": ok("r2-fixed")})


if __name__ == "__main__":
    main()
