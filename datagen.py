"""生成带人工标注的失败样例与大规模性能数据（确定性，seed 固定）。

每个"根因模板"对应一个人工标注 label；同一模板生成的多条失败只噪声不同
（随机序列号、内存地址、耗时、时间戳、UUID、行号、端口等），应被并为一组。
"""

from __future__ import annotations

import random
import string


def _noise(rng: random.Random) -> dict[str, str]:
    return {
        "addr": hex(rng.getrandbits(48)),
        "addr2": hex(rng.getrandbits(48)),
        "serial": "".join(rng.choices(string.digits, k=8)),
        "order": "ORD-" + "".join(rng.choices(string.digits, k=10)),
        "ms": str(rng.randint(1, 9000)),
        "sec": f"{rng.uniform(0.01, 30):.2f}",
        "ts": f"2026-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}T{rng.randint(0,23):02d}:{rng.randint(0,59):02d}:{rng.randint(0,59):02d}Z",
        "uuid": "%08x-%04x-%04x-%04x-%012x" % tuple(rng.getrandbits(b) for b in (32, 16, 16, 16, 48)),
        "line": str(rng.randint(10, 900)),
        "line2": str(rng.randint(10, 900)),
        "port": str(rng.randint(1024, 65000)),
        "tmp": "/tmp/" + "".join(rng.choices(string.ascii_lowercase + string.digits, k=12)),
        "token": "".join(rng.choices("0123456789abcdef", k=32)),
    }


# --- 根因模板：text 中 {key} 会被噪声替换；label 即人工标注的根因 ---

TEMPLATES: list[dict] = [
    {
        "label": "db-connection-timeout",
        "text": (
            "Traceback (most recent call last):\n"
            '  File "app/services/order.py", line {line}, in create_order\n'
            "    conn = pool.acquire(timeout=5)\n"
            '  File "app/db/pool.py", line {line2}, in acquire\n'
            "    raise TimeoutError('pool exhausted')\n"
            "TimeoutError: connection to db 10.0.0.8:{port} timed out after {ms}ms "
            "(request {order}, trace {uuid})"
        ),
    },
    {
        "label": "db-connection-refused",  # 与 timeout 相似但根因不同，不应误并
        "text": (
            "Traceback (most recent call last):\n"
            '  File "app/services/order.py", line {line}, in create_order\n'
            "    conn = pool.acquire(timeout=5)\n"
            '  File "app/db/pool.py", line {line2}, in acquire\n'
            "    raise ConnectionRefusedError('dial failed')\n"
            "ConnectionRefusedError: connect to db 10.0.0.8:{port} refused "
            "(request {order}, trace {uuid})"
        ),
    },
    {
        "label": "assert-total-price",
        "text": (
            "Traceback (most recent call last):\n"
            '  File "tests/test_checkout.py", line {line}, in test_total\n'
            "    self.assertEqual(order.total, expected)\n"
            "AssertionError: Decimal('99.90') != Decimal('9.99') "
            "[case {serial}, run at {ts}, took {ms}ms]"
        ),
    },
    {
        "label": "assert-currency",
        "text": (
            "Traceback (most recent call last):\n"
            '  File "tests/test_checkout.py", line {line}, in test_currency\n'
            "    self.assertEqual(order.currency, 'CNY')\n"
            "AssertionError: 'USD' != 'CNY' [case {serial}, took {ms}ms]"
        ),
    },
    {
        "label": "npe-user-profile",
        "text": (
            "java.lang.NullPointerException: Cannot invoke \"User.getProfile()\" "
            "because \"user\" is null\n"
            "\tat com.shop.UserService.render(UserService.java:{line})\n"
            "\tat com.shop.Web.handle(Web.java:{line2})\n"
            "requestId={uuid} elapsed={sec}s session={token}"
        ),
    },
    {
        "label": "keyerror-config",
        "text": (
            "Traceback (most recent call last):\n"
            '  File "app/config.py", line {line}, in load\n'
            "    return cfg['feature_flags']\n"
            "KeyError: 'feature_flags' (pid {serial}, started {ts})"
        ),
    },
    {
        "label": "oom-image-resize",
        "text": (
            "Traceback (most recent call last):\n"
            '  File "worker/resize.py", line {line}, in run\n'
            "    img = Image.open(src)\n"
            "MemoryError: cannot allocate buffer for image <Image object at {addr}> "
            "job={serial} tmp={tmp} elapsed {sec}s"
        ),
    },
    {
        "label": "http-502-upstream",
        "text": (
            "requests.exceptions.HTTPError: 502 Bad Gateway for "
            "https://api.internal:{port}/v1/pay (upstream took {ms}ms, "
            "req {order}, ts {ts}, token {token})"
        ),
    },
    {
        "label": "flake8-lint-only",  # 无堆栈、无标准错误行 -> 堆栈缺失路径
        "text": "lint failure: E501 line too long (132 > 120) in report #{serial}, scan took {sec}s",
    },
    {
        "label": "generic-crash-no-stack",  # 堆栈完全缺失，只有错误行
        "text": "RuntimeError: worker crashed, dump at {addr}, uptime {sec}s, seq {serial}",
    },
]


def generate_labeled(per_label: int = 40, seed: int = 20260928) -> list[dict]:
    """生成带标注样例：每条 {"id", "text", "label"}。"""
    rng = random.Random(seed)
    out: list[dict] = []
    n = 0
    for tpl in TEMPLATES:
        for _ in range(per_label):
            n += 1
            out.append({
                "id": f"F-{n:05d}",
                "text": tpl["text"].format(**_noise(rng)),
                "label": tpl["label"],
            })
    rng.shuffle(out)
    return out


def generate_bulk(total: int = 20000, seed: int = 7) -> list[dict]:
    """生成大规模失败数据（含噪声），用于性能测试。"""
    rng = random.Random(seed)
    out: list[dict] = []
    for i in range(total):
        tpl = TEMPLATES[rng.randrange(len(TEMPLATES))]
        out.append({"id": f"B-{i:06d}", "text": tpl["text"].format(**_noise(rng))})
    return out
