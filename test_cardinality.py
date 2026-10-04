"""cardinality 库自测：python3 test_cardinality.py 或 python3 -m unittest -v"""

import unittest

from cardinality import (
    OTHER,
    CardinalityTracker,
    LabelNormalizer,
    MetricStore,
    looks_like_id,
)


def make_labels(n, stable=True, growing=False, req_id=False):
    """生成 n 条标签样本，可按需混入持续增长值与请求号。"""
    out = []
    for i in range(n):
        labels = {"service": "api" if i % 2 == 0 else "web",
                  "status": str(200 if i % 5 else 500)}
        if growing:
            labels["user_tag"] = f"user_{i:06d}"      # 每条都新，但不像请求号
        if req_id:
            labels["request_id"] = (f"{i:08x}-1234-5678-9abc-{i:012x}")
        out.append(labels)
    return out


class TestLooksLikeId(unittest.TestCase):
    def test_id_shapes(self):
        self.assertTrue(looks_like_id("3f2b1c4a-1111-2222-3333-abcdefabcdef"))
        self.assertTrue(looks_like_id("0123456789abcdef"))
        self.assertTrue(looks_like_id("1696000000123"))
        self.assertTrue(looks_like_id("aB3dE5fG7hI9jK1l"))
        self.assertFalse(looks_like_id("api"))
        self.assertFalse(looks_like_id("200"))
        self.assertFalse(looks_like_id("user_000001"))  # 业务标签，不该误判
        self.assertFalse(looks_like_id(""))


class TestStableLabel(unittest.TestCase):
    def test_stable_not_flagged_and_kept(self):
        tracker = CardinalityTracker(window_size=50, max_cardinality=100)
        tracker.observe_many(make_labels(500))
        stats = tracker.classify()
        for key in ("service", "status"):
            st = stats[key]
            self.assertFalse(st.high_cardinality, key)
            self.assertEqual(st.kind, "stable")
            self.assertLessEqual(st.distinct, 5)
        normalizer = LabelNormalizer(stats)
        labels = {"service": "api", "status": "200"}
        self.assertEqual(normalizer.normalize(labels), labels)
        self.assertEqual(sum(normalizer.dropped_count.values()), 0)


class TestGrowingLabel(unittest.TestCase):
    def test_growth_detected_with_reasons(self):
        tracker = CardinalityTracker(window_size=50, max_cardinality=1000,
                                     growth_ratio_threshold=0.5)
        tracker.observe_many(make_labels(400, growing=True))
        st = tracker.classify()["user_tag"]
        self.assertTrue(st.high_cardinality)
        self.assertEqual(st.kind, "growing")
        self.assertGreaterEqual(st.growth_ratio, 0.5)
        self.assertTrue(any("growth_ratio" in r for r in st.reasons))

    def test_topk_merge_keeps_key_dimensions(self):
        tracker = CardinalityTracker(window_size=50, max_cardinality=100)
        labels = make_labels(400, growing=True)
        # 让前 3 个取值明显高频，验证 top-K 保留
        for i in range(300):
            labels[i]["user_tag"] = f"hot_{i % 3}"
        tracker.observe_many(labels)
        normalizer = LabelNormalizer(tracker.classify(), top_k=3)
        hot = normalizer.normalize({"user_tag": "hot_0"})
        cold = normalizer.normalize({"user_tag": "user_000123"})
        self.assertEqual(hot["user_tag"], "hot_0")          # 高频值保留
        self.assertEqual(cold["user_tag"], OTHER)           # 长尾归并
        self.assertEqual(normalizer.merged_sample_count["user_tag"], 1)
        mapping = normalizer.mapping_report()["user_tag"]
        self.assertEqual(mapping, [("user_000123", OTHER)])


class TestRequestIdLabel(unittest.TestCase):
    def test_request_id_dropped_with_count(self):
        tracker = CardinalityTracker(window_size=50)
        tracker.observe_many(make_labels(300, req_id=True))
        st = tracker.classify()["request_id"]
        self.assertTrue(st.high_cardinality)
        self.assertEqual(st.kind, "id_like")
        self.assertGreaterEqual(st.id_like_ratio, 0.8)
        normalizer = LabelNormalizer(tracker.classify())
        out = normalizer.normalize({"request_id": "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb",
                                    "service": "api"})
        self.assertNotIn("request_id", out)                 # 整标签丢弃
        self.assertEqual(out["service"], "api")             # 关键维度保留
        self.assertEqual(normalizer.dropped_count["request_id"], 1)


class TestAfterMergeQuery(unittest.TestCase):
    def test_query_comparison_before_after(self):
        tracker = CardinalityTracker(window_size=50)
        samples = make_labels(400, growing=True, req_id=True)
        tracker.observe_many(samples)
        normalizer = LabelNormalizer(tracker.classify(), top_k=5)

        before, after = MetricStore(), MetricStore()
        for i, labels in enumerate(samples):
            before.record("http_requests", labels)
            after.record("http_requests", normalizer.normalize(labels))

        # 归并前：每个 user_tag 都是一条序列，基数爆炸
        self.assertEqual(before.series_count("http_requests"), 400)
        # 归并后：序列数被压住
        self.assertLess(after.series_count("http_requests"), 30)

        # 关键维度（service）聚合结果前后一致
        gb_before = {r["labels"]["service"]: r["sum"]
                     for r in before.query("http_requests", group_by=["service"])}
        gb_after = {r["labels"]["service"]: r["sum"]
                    for r in after.query("http_requests", group_by=["service"])}
        self.assertEqual(gb_before, gb_after)
        self.assertEqual(gb_after, {"api": 200.0, "web": 200.0})

        # 总量守恒
        self.assertEqual(before.totals("http_requests"),
                         after.totals("http_requests"))

        # 归并后仍能区分归并桶与保留桶
        rows = after.query("http_requests", group_by=["user_tag"])
        buckets = {r["labels"]["user_tag"] for r in rows}
        self.assertIn(OTHER, buckets)

    def test_where_filter(self):
        store = MetricStore()
        store.record("m", {"service": "api", "status": "200"}, 3)
        store.record("m", {"service": "api", "status": "500"}, 1)
        store.record("m", {"service": "web", "status": "200"}, 2)
        rows = store.query("m", group_by=["status"], where={"service": "api"})
        self.assertEqual({r["labels"]["status"]: r["sum"] for r in rows},
                         {"200": 3.0, "500": 1.0})


class TestBoundary(unittest.TestCase):
    def test_empty_and_missing(self):
        tracker = CardinalityTracker()
        tracker.observe({})
        self.assertEqual(tracker.report(), [])
        normalizer = LabelNormalizer(tracker.classify())
        self.assertEqual(normalizer.normalize({}), {})
        self.assertEqual(MetricStore().query("nothing"), [])

    def test_none_and_unicode_values(self):
        tracker = CardinalityTracker(window_size=2, max_cardinality=100)
        tracker.observe({"k": None})
        tracker.observe({"k": "服务-甲"})
        tracker.observe({"k": "服务-甲"})
        st = tracker.classify()["k"]
        self.assertEqual(st.distinct, 2)
        self.assertFalse(st.high_cardinality)
        normalizer = LabelNormalizer(tracker.classify())
        self.assertEqual(normalizer.normalize({"k": None}), {"k": ""})
        self.assertEqual(normalizer.normalize({"k": "服务-甲"}), {"k": "服务-甲"})

    def test_exact_threshold_is_high(self):
        tracker = CardinalityTracker(window_size=10, max_cardinality=4,
                                     growth_ratio_threshold=0.99,
                                     id_ratio_threshold=0.99)
        for i in range(4):
            tracker.observe({"k": f"v{i}"})
        st = tracker.classify()["k"]
        self.assertTrue(st.high_cardinality)  # distinct == max_cardinality 即越界

    def test_below_min_distinct_growth_not_flagged(self):
        tracker = CardinalityTracker(window_size=2, max_cardinality=1000,
                                     min_distinct=8)
        for i in range(6):
            tracker.observe({"k": f"v{i}"})  # 增长比=1 但 distinct=6 < 8
        self.assertFalse(tracker.classify()["k"].high_cardinality)

    def test_window_size_one(self):
        tracker = CardinalityTracker(window_size=1, max_cardinality=1000,
                                     min_distinct=2)
        for i in range(3):
            tracker.observe({"k": f"v{i}"})
        st = tracker.classify()["k"]
        self.assertEqual(st.window_samples, [1, 1, 1])
        self.assertEqual(st.growth_ratio, 1.0)

    def test_mapping_cap_overflow(self):
        tracker = CardinalityTracker(window_size=10, max_cardinality=1000)
        tracker.observe_many({"k": f"v{i}"} for i in range(50))
        normalizer = LabelNormalizer(tracker.classify(), top_k=1, mapping_cap=5)
        for i in range(50):
            normalizer.normalize({"k": f"v{i}"})
        self.assertEqual(len(normalizer.mapping), 5)
        self.assertEqual(normalizer.mapping_overflow, 44)  # 49 个长尾 - 5 条上限
        self.assertEqual(normalizer.merged_sample_count["k"], 49)

    def test_unknown_label_passes_through(self):
        normalizer = LabelNormalizer({})
        self.assertEqual(normalizer.normalize({"new_label": "x"}),
                         {"new_label": "x"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
