"""tracectx 自测：python3 -m unittest discover -s tests -v"""

import asyncio
import dataclasses
import json
import os
import queue
import subprocess
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracectx import (
    InvalidContextError,
    TamperedContextError,
    TraceValidator,
    deserialize,
    extract,
    inject,
    serialize,
    start_root_span,
    start_span,
    use_span,
)
from tracectx.propagation import CARRIER_KEY

KEY = b"test-secret-key"


class TestSerialization(unittest.TestCase):
    def test_deterministic_format(self):
        span = start_root_span("root", sampled=True)
        s1 = serialize(span.context, key=KEY)
        s2 = serialize(span.context, key=KEY)
        self.assertEqual(s1, s2)  # 同一上下文序列化结果确定
        parts = s1.split(".")
        self.assertEqual(len(parts), 6)
        self.assertEqual(parts[0], "tc1")
        self.assertEqual(parts[4], "01")

    def test_roundtrip(self):
        parent = start_root_span("root", sampled=False)
        child = start_span("child", parent)
        raw = serialize(child.context, key=KEY)
        ctx = deserialize(raw, key=KEY)
        self.assertEqual(ctx, child.context)
        self.assertEqual(ctx.parent_span_id, parent.span_id)
        self.assertFalse(ctx.sampled)

    def test_root_parent_dash(self):
        root = start_root_span("root")
        raw = serialize(root.context)
        self.assertEqual(raw.split(".")[3], "-")
        ctx = deserialize(raw)
        self.assertIsNone(ctx.parent_span_id)


class TestNoContextEntry(unittest.TestCase):
    def test_extract_empty_carrier_returns_none(self):
        self.assertIsNone(extract({}))

    def test_span_without_parent_starts_new_trace(self):
        span = start_span("orphan")
        self.assertIsNone(span.parent_span_id)
        self.assertEqual(len(span.trace_id), 32)


class TestSamplingPropagation(unittest.TestCase):
    def test_sampling_decision_propagates_across_boundaries(self):
        # 上游（入口）决定不采样
        root = start_root_span("entry", sampled=False)
        carrier = inject(root, {}, key=KEY)

        # 下游服务：提取后建子 Span，再经 MQ 传给后台任务
        downstream_ctx = extract(carrier, key=KEY)
        self.assertFalse(downstream_ctx.sampled)
        mq_msg = inject(start_span("enqueue", downstream_ctx), {}, key=KEY)

        worker_ctx = extract(mq_msg, key=KEY)
        worker_span = start_span("background-job", worker_ctx)
        self.assertFalse(worker_span.sampled)
        self.assertEqual(worker_span.trace_id, root.trace_id)

    def test_downstream_cannot_change_sampling(self):
        root = start_root_span("entry", sampled=True)
        ctx = extract(inject(root, {}, key=KEY), key=KEY)
        # SpanContext 不可变：下游没有重采样接口，强行修改会抛异常
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ctx.sampled = False
        child = start_span("child", ctx)
        self.assertTrue(child.sampled)  # 只能继承上游决策


class TestTampering(unittest.TestCase):
    def test_flipped_flag_detected(self):
        span = start_root_span("root", sampled=True)
        raw = serialize(span.context, key=KEY)
        # 攻击者/故障把采样位 01 改成 00
        tampered = raw.replace(".01.", ".00.")
        with self.assertRaises(TamperedContextError):
            deserialize(tampered, key=KEY)

    def test_modified_span_id_detected(self):
        span = start_root_span("root")
        raw = serialize(span.context, key=KEY)
        parts = raw.split(".")
        parts[2] = "0" * 16  # 篡改 span_id
        with self.assertRaises(TamperedContextError):
            deserialize(".".join(parts), key=KEY)

    def test_stripped_signature_detected(self):
        span = start_root_span("root")
        raw = serialize(span.context, key=KEY)
        unsigned = ".".join(raw.split(".")[:5])  # 去掉签名
        with self.assertRaises(TamperedContextError):
            deserialize(unsigned, key=KEY)

    def test_garbage_rejected(self):
        with self.assertRaises(InvalidContextError):
            deserialize("not-a-trace-context")
        with self.assertRaises(InvalidContextError):
            deserialize("tc1.zzz.aaa.-.01")


class TestConcurrentChildren(unittest.TestCase):
    def test_asyncio_concurrent_children(self):
        validator = TraceValidator()

        async def main():
            root = start_root_span("request", sampled=True)
            validator.record(root)

            async def child(i):
                # contextvars 保证并发任务各自拿到正确的父上下文
                with use_span(root):
                    span = start_span(f"child-{i}")
                    await asyncio.sleep(0.001 * (i % 3))
                    validator.record(span)
                    return span

            return await asyncio.gather(*[child(i) for i in range(10)])

        spans = asyncio.run(main())
        span_ids = {s.span_id for s in spans}
        self.assertEqual(len(span_ids), 10)  # 并发下 span_id 互不重复
        for s in spans:
            self.assertEqual(s.parent_span_id, spans[0].parent_span_id)
        self.assertEqual(validator.validate(spans[0].trace_id), [])

    def test_queue_and_thread_boundary(self):
        """MQ 边界：carrier 必须显式随消息传递。"""
        validator = TraceValidator()
        q: queue.Queue = queue.Queue()

        root = start_root_span("api-handler", sampled=True)
        validator.record(root)
        msg = {"body": "job-payload"}
        inject(root, msg, key=KEY)  # 入队前显式注入
        q.put(msg)

        def worker():
            msg = q.get()
            ctx = extract(msg, key=KEY)  # 出队后显式提取
            span = start_span("worker", ctx)
            validator.record(span)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        self.assertEqual(validator.validate(root.trace_id), [])
        worker_span = validator._spans[root.trace_id][1]
        self.assertEqual(worker_span.parent_span_id, root.span_id)
        self.assertEqual(worker_span.trace_id, root.trace_id)


class TestCrossProcess(unittest.TestCase):
    def test_context_via_subprocess_env(self):
        root = start_root_span("parent-proc", sampled=False)
        env = dict(os.environ)
        inject(root, env, key=KEY)
        env["TRACE_HMAC_KEY"] = KEY.decode()

        child_code = """
import os, sys, json
sys.path.insert(0, {repo!r})
from tracectx import extract, start_span, serialize
key = os.environ["TRACE_HMAC_KEY"].encode()
ctx = extract(os.environ, key=key)
span = start_span("child-proc", ctx)
print(json.dumps({{"raw": serialize(span.context, key=key),
                  "sampled": span.sampled}}))
""".format(repo=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        out = subprocess.run(
            [sys.executable, "-c", child_code],
            env=env, capture_output=True, text=True, check=True,
        )
        result = json.loads(out.stdout)
        child_ctx = deserialize(result["raw"], key=KEY)

        self.assertEqual(child_ctx.trace_id, root.trace_id)       # 同一链路
        self.assertEqual(child_ctx.parent_span_id, root.span_id)  # 父子关系跨进程保留
        self.assertFalse(child_ctx.sampled)                       # 采样决策跨进程保留
        self.assertFalse(result["sampled"])

        # 子进程上下文在父进程可正常校验入链
        validator = TraceValidator()
        validator.record(root)
        from tracectx.context import Span
        validator.record(Span(name="child-proc", context=child_ctx))
        self.assertEqual(validator.validate(root.trace_id), [])


class TestIntegrityValidation(unittest.TestCase):
    def test_missing_parent_detected(self):
        from tracectx.context import Span, SpanContext
        validator = TraceValidator()
        root = start_root_span("root")
        validator.record(root)
        # 模拟 MQ 处断链：后台任务凭空挂了一个不存在的父节点
        orphan = Span(
            name="broken-worker",
            context=SpanContext(
                trace_id=root.trace_id,
                span_id="f" * 16,
                parent_span_id="0" * 16,  # 不存在于本 trace
                sampled=root.sampled,
            ),
        )
        validator.record(orphan)
        violations = validator.validate(root.trace_id)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].kind, "missing_parent")
        self.assertIn("0" * 16, violations[0].detail)

    def test_duplicate_span_id_detected(self):
        validator = TraceValidator()
        root = start_root_span("root")
        validator.record(root)
        validator.record(root)  # 同一 span 被上报两次（重试/重复消费）
        violations = validator.validate(root.trace_id)
        kinds = {v.kind for v in violations}
        self.assertIn("duplicate_span_id", kinds)

    def test_healthy_chain_passes(self):
        validator = TraceValidator()
        root = start_root_span("root")
        validator.record(root)
        a = start_span("svc-a", root)
        b = start_span("svc-b", a)
        validator.record(a)
        validator.record(b)
        self.assertEqual(validator.validate(root.trace_id), [])
        self.assertIn("OK", validator.report(root.trace_id))


if __name__ == "__main__":
    unittest.main()
