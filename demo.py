"""演示：模拟 http_requests 指标，展示识别 -> 归并/丢弃 -> 查询对比全过程。

运行：python3 demo.py
"""

import uuid

from cardinality import OTHER, CardinalityTracker, LabelNormalizer, MetricStore

N = 1000
SERVICES = ["api", "web", "job"]
STATUSES = ["200", "200", "200", "200", "404", "500"]  # 200 占多数
HOT_TENANTS = ["tenant_alice", "tenant_bob", "tenant_carol"]  # 高频租户


def gen(n=N):
    for i in range(n):
        labels = {
            "service": SERVICES[i % len(SERVICES)],
            "status": STATUSES[i % len(STATUSES)],
            # 90% 流量集中在 3 个租户，其余每请求一个新租户（持续增长）
            "tenant": HOT_TENANTS[i % 3] if i % 10 < 9 else f"tenant_{i:06d}",
            "request_id": str(uuid.UUID(int=i)),  # 请求号：几乎全唯一，丢弃
        }
        yield labels, 1.0


def table(rows):
    widths = [max(len(str(r[j])) for r in rows) for j in range(len(rows[0]))]
    for r in rows:
        print("  " + "  ".join(str(c).ljust(widths[j]) for j, c in enumerate(r)))


def main():
    samples = list(gen())

    print("=" * 72)
    print("1) 按窗口观察取值（window_size=100）")
    print("=" * 72)
    tracker = CardinalityTracker(window_size=100, max_cardinality=100,
                                 growth_ratio_threshold=0.5)
    tracker.observe_many(labels for labels, _ in samples)
    stats = tracker.classify()

    rows = [["label", "samples", "distinct", "windows",
             "growth_ratio", "id_like_ratio", "kind", "high_card?"]]
    for rep in tracker.report():
        rows.append([rep["key"], rep["sample_count"], rep["distinct"],
                     rep["windows"], rep["growth_ratio"], rep["id_like_ratio"],
                     rep["kind"], rep["high_cardinality"]])
    table(rows)

    print("\n识别依据：")
    for rep in tracker.report():
        if rep["reasons"]:
            print(f"  {rep['key']}: " + "; ".join(rep["reasons"]))

    print("\n" + "=" * 72)
    print("2) 归并 / 丢弃（growing -> top-K+other；id_like -> 整标签丢弃）")
    print("=" * 72)
    normalizer = LabelNormalizer(stats, top_k=3)
    before, after = MetricStore(), MetricStore()
    for labels, value in samples:
        before.record("http_requests", labels, value)
        after.record("http_requests", normalizer.normalize(labels), value)

    rep = normalizer.to_report()
    print("丢弃的标签:", rep["dropped_labels"])
    print("丢弃样本计数:", rep["dropped_sample_count"])
    print("归并到", OTHER, "的样本计数:",
          {k: v["samples"] for k, v in rep["merged_to_other"].items()})

    mapping = normalizer.mapping_report()
    print("\n归并映射（原取值 -> 归并桶，展示前 8 条）：")
    shown = 0
    for key, pairs in mapping.items():
        for value, bucket in pairs[:8]:
            print(f"  {key}[{value}] -> {bucket}")
            shown += 1
            if shown >= 8:
                break
        if shown >= 8:
            break
    total_mapped = sum(len(p) for p in mapping.values())
    print(f"  （映射共 {total_mapped} 条，高频租户 {HOT_TENANTS} 原样保留）")

    print("\n" + "=" * 72)
    print("3) 归并前后查询结果对比（指标 http_requests）")
    print("=" * 72)
    print(f"  时间序列条数（基数）: 归并前 {before.series_count('http_requests')}"
          f" -> 归并后 {after.series_count('http_requests')}")

    print("\n  按关键维度 service/status 聚合（sum），前后应一致：")
    qb = before.query("http_requests", group_by=["service", "status"])
    qa = after.query("http_requests", group_by=["service", "status"])
    rows = [["service", "status", "before_sum", "after_sum"]]
    for rb, ra in zip(qb, qa):
        rows.append([rb["labels"]["service"], rb["labels"]["status"],
                     rb["sum"], ra["sum"]])
    table(rows)
    assert qb == qa, "关键维度聚合结果不一致！"
    print("  -> 前后逐行一致；总请求数:",
          before.totals("http_requests"), "==", after.totals("http_requests"))

    print("\n  按 tenant 聚合：归并前分组数多且不断增长，归并后被压住但仍可区分：")
    qa = after.query("http_requests", group_by=["tenant"])
    table([["tenant", "sum"]] +
          [[r["labels"]["tenant"], r["sum"]] for r in qa])
    print("  -> 3 个高频租户各自可查，长尾统一进", OTHER, "；request_id 维度已删除。")


if __name__ == "__main__":
    main()
