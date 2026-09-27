"""端到端演示：API 服务 -> MQ -> 后台 worker -> 并发子任务，最后校验链路完整性。

运行: python3 demo.py
"""

import asyncio
import os
import queue
import threading

from tracectx import TraceValidator, extract, inject, start_root_span, start_span, use_span

KEY = os.environ.get("TRACE_HMAC_KEY", "demo-key").encode()
validator = TraceValidator()


def api_service(q: queue.Queue, sampled: bool) -> str:
    """入口服务：无上下文进入，做出采样决策。"""
    root = start_root_span("POST /orders", sampled=sampled)
    validator.record(root)
    msg = {"order_id": "A-1001"}
    inject(root, msg, key=KEY)  # MQ 边界：显式注入
    q.put(msg)
    return root.trace_id


def worker_service(q: queue.Queue) -> None:
    """后台任务：从 MQ 取消息，显式提取上下文，再派生并发子任务。"""
    msg = q.get()
    ctx = extract(msg, key=KEY)  # MQ 边界：显式提取
    job = start_span("process-order", ctx)
    validator.record(job)

    async def fan_out():
        async def subtask(name):
            with use_span(job):
                span = start_span(name)
                await asyncio.sleep(0.01)
                validator.record(span)

        await asyncio.gather(subtask("charge-payment"), subtask("send-email"))

    asyncio.run(fan_out())


def main():
    q: queue.Queue = queue.Queue()
    trace_id = api_service(q, sampled=True)
    t = threading.Thread(target=worker_service, args=(q,))
    t.start()
    t.join()

    print(f"trace_id = {trace_id}")
    print(validator.report(trace_id))
    print("\n链路结构:")
    for span in validator._spans[trace_id]:
        indent = "  " if span.parent_span_id else ""
        print(f"{indent}- {span.name} (span={span.span_id}, parent={span.parent_span_id}, sampled={span.sampled})")


if __name__ == "__main__":
    main()
