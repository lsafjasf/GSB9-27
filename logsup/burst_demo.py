"""突发场景量化：抑制前后写入量、抑制率、关键信息保留条数。

场景 A：单条高频模板风暴（故障期典型：同一错误每秒 2 万条）
场景 B：海量不同模板风暴（每条日志都略有不同的攻击/扫描流量）
场景 C：混合故障现场（高频噪声 + 偶发新错误模式 + 关键参数）
"""

from log_suppressor import LogSuppressor


def report(name, sup, outputs, key_messages):
    s = sup.stats()
    retained = sum(1 for m in key_messages if m in outputs)
    print(f"\n=== {name} ===")
    print(f"输入条数        : {s['received']:>10,}")
    print(f"输出条数        : {s['emitted']:>10,}  (首条 + 周期汇总 + 淘汰冲刷)")
    print(f"抑制率          : {s['suppression_rate']*100:>10.4f} %")
    print(f"写入字节 前/后  : {s['bytes_in']:>10,} -> {s['bytes_out']:,}  "
          f"(减少 {s['byte_reduction']*100:.2f}%)")
    print(f"关键信息保留    : {retained}/{len(key_messages)} 条首条全量保留")
    print(f"模板状态上界    : {s['tracked_templates']:,} 个在册, 已淘汰 {s['evicted']:,}")
    samples = [l for l in outputs if l.startswith("[SUPPRESSED")][:2]
    for line in samples:
        print(f"汇总样例        : {line[:120]}")


def scenario_a():
    sup = LogSuppressor(interval=10.0)
    outputs, t = [], 0.0
    hot = "disk io error dev=/dev/sda1 sector=8842 cost=120ms"
    for sec in range(30):
        for _ in range(20_000):
            outputs += sup.process(hot, now=t)
        t += 1.0
    outputs += sup.flush(now=t)
    report("场景 A：单条高频模板 (30s × 20k/s)", sup, outputs, [hot])


def scenario_b():
    sup = LogSuppressor(interval=10.0, max_templates=1_000)
    outputs = []
    keys = []
    for i in range(50_000):  # 每条都是全新模板
        n, word = i, ""
        while True:
            word = chr(97 + n % 26) + word
            n = n // 26 - 1
            if n < 0:
                break
        msg = f"scan attempt signature-{word} detected"
        if i < 3:
            keys.append(msg)
        outputs += sup.process(msg, now=float(i) / 100)
    outputs += sup.flush(now=1e9)
    s = sup.stats()
    print(f"\n=== 场景 B：海量不同模板 (50k 条全不同, 上界 1000) ===")
    print(f"输入条数        : {s['received']:>10,}")
    print(f"输出条数        : {s['emitted']:>10,}  (每个新模板首条必须放行, 无法抑制)")
    print(f"模板状态上界    : 在册 {s['tracked_templates']:,} <= 1000, 已淘汰 {s['evicted']:,}")
    print(f"内存结论        : 输入 50k 条而状态恒定 <=1000 个模板, 抑制器不随流量膨胀")


def scenario_c():
    sup = LogSuppressor(interval=10.0, max_templates=10_000)
    outputs, t = [], 0.0
    noise = "healthcheck ok latency=3ms node=10.0.0.1"
    keys = [
        "ERROR raid controller degraded slot=3",
        "ERROR filesystem remounted read-only dev=/dev/sdb1",
        "FATAL kernel hung task sync blocked 120s",
    ]
    for sec in range(60):
        for _ in range(20_000):
            outputs += sup.process(noise, now=t)
        if sec in (5, 25, 45):  # 故障中途首次出现的新错误模式
            outputs += sup.process(keys[sec // 20 if sec < 45 else 2], now=t)
        t += 1.0
    outputs += sup.flush(now=t)
    report("场景 C：混合故障现场 (60s, 噪声 20k/s + 3 个新错误模式)", sup, outputs, keys)


if __name__ == "__main__":
    scenario_a()
    scenario_b()
    scenario_c()
