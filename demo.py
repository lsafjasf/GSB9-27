"""End-to-end demo of trace context propagation.

Pipeline: ingress (no upstream context) -> service-a -> message queue
-> worker -> concurrent background tasks -> separate OS process.
Every hop passes the context explicitly through a carrier; all spans are
collected and the trace integrity is validated at the end.

Usage:
    python3 demo.py                      # run the full pipeline
    python3 demo.py --subprocess-worker  # internal: child-process entry
"""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from tracelib import (
    HEADER_NAME,
    IntegrityValidator,
    SpanRecord,
    TamperedContextError,
    TraceContext,
)

SECRET = b"demo-shared-secret"


def subprocess_worker() -> None:
    """Runs in a child OS process: context arrives via an env-var carrier."""
    carrier = json.loads(os.environ["TRACE_CARRIER"])
    ctx = TraceContext.extract(carrier, SECRET)
    worker = ctx.child()
    records = [SpanRecord.from_context(worker, "subprocess-worker", "handle")]
    records.append(SpanRecord.from_context(worker.child(), "subprocess-worker", "sub-task"))
    for rec in records:
        print(json.dumps(rec.__dict__))


def background_task(ctx: TraceContext, name: str, validator: IntegrityValidator) -> None:
    task = ctx.child()
    validator.record(SpanRecord.from_context(task, "background", name))


def main() -> int:
    validator = IntegrityValidator()

    # 1. Entry with no upstream context: root + the single sampling decision.
    ingress = TraceContext.root(SECRET, sampled=True)
    validator.record(SpanRecord.from_context(ingress, "ingress", "GET /orders"))

    # 2. In-process service call.
    api = ingress.child()
    validator.record(SpanRecord.from_context(api, "service-a", "create-order"))

    # 3. Queue boundary: context travels explicitly in message metadata.
    message = {"payload": {"order_id": 42}, "meta": api.inject({})}

    # 4. Consumer extracts the context from the message.
    worker = TraceContext.extract(message["meta"], SECRET).child()
    validator.record(SpanRecord.from_context(worker, "worker", "process-order"))

    # 5. Concurrent background tasks, each gets its own child context.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name in ("charge", "notify", "audit", "sync"):
            pool.submit(background_task, worker, name, validator)

    # 6. Cross-process hop: carrier serialized into the child process env.
    env = dict(os.environ, TRACE_CARRIER=json.dumps(worker.inject({})))
    out = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--subprocess-worker"],
        env=env, capture_output=True, text=True, check=True,
    )
    for line in out.stdout.splitlines():
        validator.record(SpanRecord(**json.loads(line)))

    # 7. Tamper attempt: flip the sampled flag on the wire.
    parts = message["meta"][HEADER_NAME].split(".")
    parts[4] = "0" if parts[4] == "1" else "1"
    try:
        TraceContext.deserialize(".".join(parts), SECRET)
        print("tamper check: FAILED (tampering not detected!)")
        return 1
    except TamperedContextError as exc:
        print(f"tamper check: detected -> {exc}")

    # 8. Integrity validation over the whole collected trace.
    print(f"spans collected: 8 across 5 services/processes")
    print(validator.validate())
    return 0 if validator.validate().ok else 1


if __name__ == "__main__":
    if "--subprocess-worker" in sys.argv:
        subprocess_worker()
    else:
        sys.exit(main())
