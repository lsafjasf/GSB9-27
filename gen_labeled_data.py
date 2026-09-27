#!/usr/bin/env python3
"""gen_labeled_data.py — 生成带人工标注的失败样例 labeled_failures.json（确定性，可复现）"""
import json
import random

rng = random.Random(20260928)


def noise():
    return {
        'seq': rng.randint(10000, 99999999),
        'dur': rng.randint(1, 90000),
        'line': rng.randint(10, 400),
        'hex': '%x' % rng.getrandbits(48),
        'uuid': '%08x-%04x-%04x-%04x-%012x' % tuple(rng.getrandbits(4 * x) for x in (8, 4, 4, 4, 12)),
        'ts': '2026-09-%02d %02d:%02d:%02d' % (rng.randint(1, 28), rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59)),
    }


# 每个根因：label -> 若干报文模板（同根因的不同表面形态）
ROOT_CAUSES = {
    'keyerror-price': [
        'Traceback (most recent call last):\n  File "/app/tests/test_order.py", line {line}, in test_create\n    create_order(items)\n  File "/app/svc/order.py", line {line}, in create_order\n    total = sum(i["price"] for i in items)\nKeyError: \'price\'  [request_id=req-{seq}, took {dur}ms]',
        'Traceback (most recent call last):\n  File "/app/jobs/retry.py", line {line}, in <module>\n    create_order(payload)\n  File "/app/svc/order.py", line {line}, in create_order\n    total = sum(i["price"] for i in items)\nKeyError: \'price\'  [request_id=req-{seq}, took {dur}ms]',
    ],
    'npe-user-null': [
        'java.lang.NullPointerException: Cannot invoke "String.length()" because "user" is null\n\tat com.shop.UserService.getProfile(UserService.java:{line})\n\tat com.shop.UserController.profile(UserController.java:{line})\n\tat com.shop.Main.main(Main.java:{line})\n\t... trace_id={uuid}',
    ],
    'assert-status-500': [
        'AssertionError: expected status 200 but got 500 for POST /orders (request_id=req-{seq}, took {dur}ms, at {ts})',
        'AssertionError: expected status 200 but got 500 for POST /orders\n  context: attempt={seq} elapsed={dur}s trace={uuid}',
    ],
    'assert-status-404': [
        'AssertionError: expected status 200 but got 404 for GET /users/{seq} (took {dur}ms)',
    ],
    'redis-conn-refused': [
        'ConnectionRefusedError: [Errno 111] Connection refused: redis://10.0.0.9:6379 (attempt 3, waited {dur}ms, pid={seq})',
    ],
    'query-timeout': [
        'TimeoutError: SQL query exceeded deadline of {dur}ms\n  File "/app/dao/report.py", line {line}, in run_report\n    cur.execute(SQL)\n  File "/app/dao/report.py", line {line}, in <module>\n    run_report()  # obj at 0x{hex}',
    ],
    'missing-config': [
        'FileNotFoundError: [Errno 2] No such file or directory: \'/etc/app/conf-{seq}.yaml\'\n  File "/app/boot.py", line {line}, in load_config\n    open(path)\n  File "/app/boot.py", line {line}, in main\n    load_config()',
    ],
    'log-permission': [
        'PermissionError: [Errno 13] Permission denied: \'/var/log/app/{seq}.log\' (uid=1000, obj 0x{hex})',
    ],
    'assert-list-diff': [
        'AssertionError: lists differ: [1, 2, 3] != [1, 2, 4]\nFirst differing element 2: 3 != 4 (case #{seq}, seed={seq})',
    ],
    'oom-heap': [
        'java.lang.OutOfMemoryError: Java heap space\n\tat java.util.Arrays.copyOf(Arrays.java:{line})\n\tat com.shop.ReportBuilder.build(ReportBuilder.java:{line})\n\tat com.shop.Job.run(Job.java:{line})  # alloc at 0x{hex}, used {dur}ms',
    ],
    'typeerror-concat': [
        'TypeError: can only concatenate str (not "int") to str\n  File "/app/util/fmt.py", line {line}, in render\n    return prefix + count\n  File "/app/api/view.py", line {line}, in get\n    render(c)  [trace_id={uuid}]',
    ],
    'decode-utf8': [
        'UnicodeDecodeError: \'utf-8\' codec can\'t decode byte 0xff in position 12: invalid start byte\n  File "/app/io/loader.py", line {line}, in load\n    raw.decode("utf-8")  (file=/tmp/blob-{seq}.bin, read {dur}ms)',
    ],
}

# 孤立失败：每条自成一根因（人工标注各不相同）
SINGLETONS = [
    ('dns-resolution', 'socket.gaierror: [Errno -2] Name or service not known: api.internal (took {dur}ms)'),
    ('ssl-expired', 'ssl.SSLCertVerificationError: certificate has expired (notAfter=2026-08-01, host=pay.example.com, req {seq})'),
    ('disk-full', 'OSError: [Errno 28] No space left on device: \'/data/shard-{seq}.db\''),
    ('assert-empty-body', 'AssertionError: expected non-empty body but got \'\' for GET /health (took {dur}ms)'),
    ('kafka-lag', 'KafkaError: consumer lag 987654 exceeds threshold (group=g-{seq})'),
    ('index-out-of-range', 'IndexError: list index out of range\n  File "/app/rank.py", line {line}, in top\n    return xs[10]'),
    ('grpc-unavailable', 'grpc.RpcError: StatusCode.UNAVAILABLE: upstream connect error (deadline {dur}ms, 0x{hex})'),
    ('json-schema', 'jsonschema.ValidationError: \'amount\' is a required property (doc {uuid})'),
]


def main():
    data = []
    for label, templates in ROOT_CAUSES.items():
        n = rng.randint(30, 60)
        for _ in range(n):
            t = rng.choice(templates)
            data.append({'label': label, 'message': t.format(**noise())})
    for label, tpl in SINGLETONS:
        data.append({'label': label, 'message': tpl.format(**noise())})
    rng.shuffle(data)
    with open('labeled_failures.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f'生成 {len(data)} 条样例，{len(ROOT_CAUSES) + len(SINGLETONS)} 个人工标注根因 -> labeled_failures.json')


if __name__ == '__main__':
    main()
