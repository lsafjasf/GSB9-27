"""Self-tests for naive_bayes.py, checked against hand-computed values.

Run:  python3 -m unittest test_naive_bayes -v   (or: python3 test_naive_bayes.py)

Hand-computed fixture (classic 4-doc example)
---------------------------------------------
Train:
    d1 = [Chinese, Beijing, Chinese]   -> c
    d2 = [Chinese, Chinese, Shanghai]  -> c
    d3 = [Chinese, Macao]              -> c
    d4 = [Tokyo, Japan, Chinese]       -> j

Vocabulary (|V| = 6): Beijing, Chinese, Japan, Macao, Shanghai, Tokyo
Class doc counts: c=3, j=1  ->  priors P(c)=3/4, P(j)=1/4
Token counts:  c: Chinese=5, Beijing=1, Shanghai=1, Macao=1 (total 8)
               j: Chinese=1, Tokyo=1, Japan=1            (total 3)

With alpha=1 (Laplace), denominator_c = 8+6 = 14, denominator_j = 3+6 = 9:
    P(Chinese|c)=6/14=3/7   P(Beijing|c)=2/14=1/7   P(Japan|c)=P(Tokyo|c)=1/14
    P(Chinese|j)=P(Japan|j)=P(Tokyo|j)=2/9

Query x = [Chinese, Chinese, Chinese, Tokyo, Japan]:
    score(c) = log(3/4) + 3*log(3/7) + 2*log(1/14) = -8.107690...
    score(j) = log(1/4) + 5*log(2/9)               = -8.906681...
    => predict "c"
"""

import math
import unittest

from naive_bayes import MultinomialNaiveBayes

DOCS = [
    ["Chinese", "Beijing", "Chinese"],
    ["Chinese", "Chinese", "Shanghai"],
    ["Chinese", "Macao"],
    ["Tokyo", "Japan", "Chinese"],
]
LABELS = ["c", "c", "c", "j"]
QUERY = ["Chinese", "Chinese", "Chinese", "Tokyo", "Japan"]

# Hand-derived expected scores (see module docstring).
EXPECTED_SCORE_C = math.log(3 / 4) + 3 * math.log(3 / 7) + 2 * math.log(1 / 14)
EXPECTED_SCORE_J = math.log(1 / 4) + 5 * math.log(2 / 9)


class HandComputedTest(unittest.TestCase):
    def setUp(self):
        self.clf = MultinomialNaiveBayes().fit(DOCS, LABELS)

    def test_priors(self):
        self.assertAlmostEqual(self.clf.class_log_prior_["c"], math.log(3 / 4))
        self.assertAlmostEqual(self.clf.class_log_prior_["j"], math.log(1 / 4))

    def test_feature_log_prob(self):
        probs_c = self.clf.feature_log_prob_["c"]
        probs_j = self.clf.feature_log_prob_["j"]
        self.assertAlmostEqual(probs_c["Chinese"], math.log(3 / 7))
        self.assertAlmostEqual(probs_c["Beijing"], math.log(1 / 7))
        self.assertAlmostEqual(probs_c["Japan"], math.log(1 / 14))
        self.assertAlmostEqual(probs_j["Chinese"], math.log(2 / 9))
        self.assertAlmostEqual(probs_j["Tokyo"], math.log(2 / 9))

    def test_smoothed_probabilities_sum_to_one(self):
        for label in ("c", "j"):
            total = sum(math.exp(v) for v in self.clf.feature_log_prob_[label].values())
            self.assertAlmostEqual(total, 1.0)

    def test_predict_scores_match_hand_computation(self):
        label, scores = self.clf.predict(QUERY)
        self.assertEqual(label, "c")
        self.assertAlmostEqual(scores["c"], EXPECTED_SCORE_C)
        self.assertAlmostEqual(scores["j"], EXPECTED_SCORE_J)
        # Sanity: no underflow, scores are finite negative numbers.
        self.assertTrue(all(math.isfinite(s) and s < 0 for s in scores.values()))


class ReproducibilityTest(unittest.TestCase):
    def test_same_data_same_params(self):
        clf1 = MultinomialNaiveBayes().fit(DOCS, LABELS)
        clf2 = MultinomialNaiveBayes().fit(DOCS, LABELS)
        self.assertEqual(clf1.class_log_prior_, clf2.class_log_prior_)
        self.assertEqual(clf1.feature_log_prob_, clf2.feature_log_prob_)
        self.assertEqual(clf1.predict(QUERY), clf2.predict(QUERY))

    def test_document_order_does_not_matter(self):
        order = [3, 0, 2, 1]
        clf = MultinomialNaiveBayes().fit(
            [DOCS[i] for i in order], [LABELS[i] for i in order]
        )
        ref = MultinomialNaiveBayes().fit(DOCS, LABELS)
        self.assertEqual(clf.class_log_prior_, ref.class_log_prior_)
        self.assertEqual(clf.feature_log_prob_, ref.feature_log_prob_)
        self.assertEqual(clf.predict(QUERY), ref.predict(QUERY))


class EdgeCaseTest(unittest.TestCase):
    def test_unseen_token_at_predict_is_ignored(self):
        clf = MultinomialNaiveBayes().fit(DOCS, LABELS)
        with_unseen = clf.predict(QUERY + ["NeverSeenBefore"])
        without = clf.predict(QUERY)
        self.assertEqual(with_unseen, without)

    def test_unseen_vocabulary_token_has_no_zero_probability(self):
        # "Japan" never appears in class "c"; smoothing keeps P > 0.
        clf = MultinomialNaiveBayes().fit(DOCS, LABELS)
        self.assertTrue(math.isfinite(clf.feature_log_prob_["c"]["Japan"]))

    def test_single_class_training_set(self):
        clf = MultinomialNaiveBayes().fit([["a", "b"], ["a"]], ["only", "only"])
        self.assertEqual(clf.classes_, ["only"])
        self.assertAlmostEqual(clf.class_log_prior_["only"], 0.0)  # log(1)
        label, scores = clf.predict(["a", "zzz"])
        self.assertEqual(label, "only")
        self.assertEqual(set(scores), {"only"})

    def test_zero_features_at_predict_gives_log_prior(self):
        clf = MultinomialNaiveBayes().fit(DOCS, LABELS)
        label, scores = clf.predict([])
        self.assertEqual(label, "c")  # majority class wins on prior alone
        self.assertAlmostEqual(scores["c"], math.log(3 / 4))
        self.assertAlmostEqual(scores["j"], math.log(1 / 4))

    def test_class_with_zero_tokens(self):
        # Class "empty" has a document with no tokens at all.
        clf = MultinomialNaiveBayes().fit([["a", "a", "b"], []], ["x", "empty"])
        label, scores = clf.predict(["a"])
        # P(a|x) = 3/5 vs P(a|empty) = 1/2, equal priors -> "x" wins.
        self.assertEqual(label, "x")
        self.assertTrue(all(math.isfinite(s) for s in scores.values()))
        # Repeated tokens widen the gap; scores stay finite (no underflow).
        _, scores = clf.predict(["a", "a", "a"])
        self.assertLess(scores["empty"], scores["x"])

    def test_uniform_prior_option(self):
        clf = MultinomialNaiveBayes(fit_prior=False).fit(DOCS, LABELS)
        self.assertAlmostEqual(clf.class_log_prior_["c"], math.log(1 / 2))
        self.assertAlmostEqual(clf.class_log_prior_["j"], math.log(1 / 2))

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            MultinomialNaiveBayes(alpha=0)
        with self.assertRaises(ValueError):
            MultinomialNaiveBayes().fit([], [])
        with self.assertRaises(ValueError):
            MultinomialNaiveBayes().fit([["a"]], ["x", "y"])
        with self.assertRaises(ValueError):
            MultinomialNaiveBayes().predict(["a"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
