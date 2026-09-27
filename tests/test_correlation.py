import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codemetrics.complexity import analyze_file
from codemetrics.stats import kendall_tau_b, spearman

ROOT = os.path.join(os.path.dirname(__file__), "..")


class TestHumanAnnotationCorrelation(unittest.TestCase):
    """The metric ranking must agree with the human-annotated ranking."""

    def test_spearman_and_kendall(self):
        ann_path = os.path.join(ROOT, "samples", "annotations.json")
        with open(ann_path, encoding="utf-8") as fh:
            annotations = json.load(fh)["functions"]
        sample = os.path.join(ROOT, "samples", "annotated.py")
        metrics = {f.qualname: f for f in analyze_file(sample).functions}
        names = sorted(annotations)
        human = [annotations[n]["human"] for n in names]
        metric = [metrics[n].complexity for n in names]
        rho = spearman(human, metric)
        tau = kendall_tau_b(human, metric)
        self.assertGreaterEqual(rho, 0.90, f"Spearman rho={rho:.3f} < 0.90")
        self.assertGreaterEqual(tau, 0.75, f"Kendall tau-b={tau:.3f} < 0.75")

    def test_long_straight_code_not_flagged(self):
        # The core motivation: f03 is 41 lines but trivial; the metric must
        # rank it far below the genuinely branchy functions.
        sample = os.path.join(ROOT, "samples", "annotated.py")
        metrics = {f.qualname: f for f in analyze_file(sample).functions}
        self.assertEqual(metrics["f03_long_straight"].complexity, 1)
        self.assertGreater(metrics["f03_long_straight"].loc, 40)
        self.assertGreater(metrics["f04_single_if"].complexity,
                           metrics["f03_long_straight"].complexity)


class TestStatsSanity(unittest.TestCase):
    def test_perfect_agreement(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(kendall_tau_b([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)

    def test_perfect_disagreement(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)
        self.assertAlmostEqual(kendall_tau_b([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)

    def test_ties(self):
        rho = spearman([1, 1, 2], [5, 5, 9])
        self.assertAlmostEqual(rho, 1.0)


if __name__ == "__main__":
    unittest.main()
