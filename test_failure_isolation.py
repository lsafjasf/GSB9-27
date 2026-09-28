"""失败隔离断言测试：传播范围、跳过原因、局部重试。仅标准库 unittest。"""

import unittest

from failure_isolation import DAGError, Orchestrator, Status


def ok(name):
    return lambda: f"done-{name}"


def boom(msg="boom"):
    def fn():
        raise RuntimeError(msg)
    return fn


class TestPropagationAndIsolation(unittest.TestCase):
    """失败只传播到依赖它的下游，无关分支必须继续。"""

    def build(self, fail_at):
        r"""结构：
              a
            /   \
           b     c        <- b 扇出失败点
          / \    |
         d   e   f
          \ /    |
           g     h        <- g 共享依赖 d,e
        i（独立根，与 a 无关）
        """
        o = Orchestrator()
        o.add_task("a", ok("a"))
        o.add_task("b", boom() if fail_at == "b" else ok("b"), deps=["a"])
        o.add_task("c", ok("c"), deps=["a"])
        o.add_task("d", ok("d"), deps=["b"])
        o.add_task("e", ok("e"), deps=["b"])
        o.add_task("f", ok("f"), deps=["c"])
        o.add_task("g", ok("g"), deps=["d", "e"])
        o.add_task("h", ok("h"), deps=["f"])
        o.add_task("i", ok("i"))
        return o

    def test_fanout_failure_isolates_only_affected_subgraph(self):
        o = self.build(fail_at="b").run()
        s = o.stats()
        # 失败与跳过分开统计
        self.assertEqual(s["failed"], ["b"])
        self.assertEqual(sorted(s["skipped"]), ["d", "e", "g"])
        # 无关分支 c/f/h 与独立根 i 必须完成
        self.assertEqual(sorted(s["completed"]), ["a", "c", "f", "h", "i"])
        # 跳过原因指向根失败 b（g 是间接跳过，也要归因到 b）
        self.assertEqual(s["skip_reasons"], {
            "d": ["b"], "e": ["b"], "g": ["b"],
        })
        # 完成率：9 个任务完成 5 个
        self.assertAlmostEqual(s["completion_rate"], 5 / 9)

    def test_affected_by_matches_transitive_closure(self):
        o = self.build(fail_at="b")
        self.assertEqual(o.affected_by({"b"}), {"d", "e", "g"})
        self.assertEqual(o.affected_by({"a"}),
                         {"b", "c", "d", "e", "f", "g", "h"})
        self.assertEqual(o.affected_by({"h"}), set())

    def test_single_point_failure(self):
        o = Orchestrator()
        o.add_task("root", boom())
        o.add_task("child", ok("child"), deps=["root"])
        o.add_task("unrelated", ok("unrelated"))
        o.run()
        s = o.stats()
        self.assertEqual(s["failed"], ["root"])
        self.assertEqual(s["skipped"], ["child"])
        self.assertEqual(s["completed"], ["unrelated"])
        self.assertEqual(s["skip_reasons"], {"child": ["root"]})

    def test_shared_dependency_failure(self):
        """共享依赖失败：所有依赖它的汇合点都被跳过，其余继续。"""
        o = Orchestrator()
        o.add_task("shared", boom())
        o.add_task("x", ok("x"), deps=["shared"])
        o.add_task("y", ok("y"), deps=["shared"])
        o.add_task("join", ok("join"), deps=["x", "y"])
        o.add_task("other", ok("other"))
        o.run()
        s = o.stats()
        self.assertEqual(s["failed"], ["shared"])
        self.assertEqual(sorted(s["skipped"]), ["join", "x", "y"])
        self.assertEqual(s["completed"], ["other"])
        for t in ("x", "y", "join"):
            self.assertEqual(s["skip_reasons"][t], ["shared"])

    def test_total_failure(self):
        """全部失败：每个根都失败，下游全跳过，完成率 0。"""
        o = Orchestrator()
        o.add_task("r1", boom())
        o.add_task("r2", boom())
        o.add_task("m", ok("m"), deps=["r1", "r2"])
        o.add_task("leaf", ok("leaf"), deps=["m"])
        o.run()
        s = o.stats()
        self.assertEqual(sorted(s["failed"]), ["r1", "r2"])
        self.assertEqual(sorted(s["skipped"]), ["leaf", "m"])
        self.assertEqual(s["completed"], [])
        self.assertEqual(s["completion_rate"], 0.0)
        # m 的跳过原因应同时列出两个失败上游
        self.assertEqual(sorted(s["skip_reasons"]["m"]), ["r1", "r2"])
        self.assertEqual(s["skip_reasons"]["leaf"], ["r1", "r2"])

    def test_validation_errors(self):
        o = Orchestrator()
        o.add_task("a", ok("a"), deps=["ghost"])
        with self.assertRaises(DAGError):
            o.run()
        o2 = Orchestrator()
        o2.add_task("a", ok("a"), deps=["b"])
        o2.add_task("b", ok("b"), deps=["a"])
        with self.assertRaises(DAGError):
            o2.run()


class TestPartialRetry(unittest.TestCase):
    """局部重试：只重跑受影响子图，已完成任务不得重复执行。"""

    def test_resume_reruns_only_affected_subgraph(self):
        o = Orchestrator()
        o.add_task("a", ok("a"))
        o.add_task("b", boom("v1 broken"), deps=["a"])
        o.add_task("c", ok("c"), deps=["a"])
        o.add_task("d", ok("d"), deps=["b"])
        o.add_task("e", ok("e"), deps=["c"])
        o.run()
        self.assertEqual(o.stats()["failed"], ["b"])
        self.assertEqual(o.stats()["skipped"], ["d"])
        completed_before = set(o.stats()["completed"])
        counts_before = dict(o.run_counts)

        # 修复 b 后局部重试
        o.resume(fixes={"b": ok("b-fixed")})
        s = o.stats()
        self.assertEqual(s["failed"], [])
        self.assertEqual(s["skipped"], [])
        self.assertEqual(sorted(s["completed"]), ["a", "b", "c", "d", "e"])
        self.assertEqual(s["completion_rate"], 1.0)

        # 已完成任务绝不重跑；只有 b 和 d 各多跑一次
        for name in completed_before:
            self.assertEqual(o.run_counts[name], counts_before[name],
                             f"{name} 被重复执行了！")
        self.assertEqual(o.run_counts["b"], 2)   # 失败 1 次 + 重试 1 次
        self.assertEqual(o.run_counts["d"], 1)   # 首轮被跳过，只执行 1 次

    def test_resume_without_fix_keeps_failure(self):
        o = Orchestrator()
        o.add_task("a", boom())
        o.add_task("b", ok("b"), deps=["a"])
        o.run()
        o.resume()  # 没有修复，重跑仍然失败，b 仍然跳过
        s = o.stats()
        self.assertEqual(s["failed"], ["a"])
        self.assertEqual(s["skipped"], ["b"])
        self.assertEqual(o.run_counts["a"], 2)
        self.assertEqual(o.run_counts.get("b", 0), 0)  # b 从未真正执行

    def test_resume_chain_after_shared_dep_fix(self):
        o = Orchestrator()
        o.add_task("shared", boom())
        o.add_task("x", ok("x"), deps=["shared"])
        o.add_task("y", ok("y"), deps=["shared"])
        o.add_task("join", ok("join"), deps=["x", "y"])
        o.run()
        self.assertEqual(o.stats()["completion_rate"], 0.0)
        o.resume(fixes={"shared": ok("shared-fixed")})
        s = o.stats()
        self.assertEqual(s["completion_rate"], 1.0)
        self.assertEqual(o.run_counts["shared"], 2)
        for t in ("x", "y", "join"):
            self.assertEqual(o.run_counts[t], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
