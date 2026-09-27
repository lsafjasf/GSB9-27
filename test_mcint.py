"""Self-tests for mcint (stdlib unittest). Run: python3 -m unittest -v"""

import math
import random
import unittest

from mcint import crude_mc, stratified_mc, importance_sampling, control_variate


class TestCrudeMC(unittest.TestCase):
    def test_constant_function_is_exact(self):
        res = crude_mc(lambda x: 3.5, dim=3, n=1000, rng=random.Random(0))
        self.assertAlmostEqual(res.estimate, 3.5, places=12)
        self.assertAlmostEqual(res.stderr, 0.0, places=12)

    def test_box_volume_scaling(self):
        # integral of 1 over [0,2]x[0,3] = 6
        res = crude_mc(lambda x: 1.0, dim=2, n=500, rng=random.Random(0),
                       lower=[0, 0], upper=[2, 3])
        self.assertAlmostEqual(res.estimate, 6.0, places=12)

    def test_polynomial_1d(self):
        # integral of x^2 over [0,1] = 1/3
        res = crude_mc(lambda x: x[0] ** 2, dim=1, n=200_000, rng=random.Random(42))
        self.assertAlmostEqual(res.estimate, 1.0 / 3.0, delta=4 * res.stderr)

    def test_stderr_shrinks_like_inverse_sqrt_n(self):
        f = lambda x: math.exp(sum(x))
        r1 = crude_mc(f, 3, 10_000, random.Random(7))
        r2 = crude_mc(f, 3, 160_000, random.Random(7))  # 16x samples -> se/4
        ratio = r1.stderr / r2.stderr
        self.assertGreater(ratio, 2.5)
        self.assertLess(ratio, 6.5)

    def test_rng_injection_reproducible(self):
        f = lambda x: sum(x)
        a = crude_mc(f, 2, 1000, random.Random(123))
        b = crude_mc(f, 2, 1000, random.Random(123))
        self.assertEqual(a.estimate, b.estimate)


class TestStratified(unittest.TestCase):
    def test_constant_exact(self):
        res = stratified_mc(lambda x: 2.0, dim=2, n=1600, rng=random.Random(0), strata=4)
        self.assertAlmostEqual(res.estimate, 2.0, places=12)

    def test_lower_variance_than_crude_on_smooth(self):
        f = lambda x: math.exp(sum(x))
        n = 100_000
        crude = crude_mc(f, 2, n, random.Random(5))
        strat = stratified_mc(f, 2, n, random.Random(5), strata=16)
        self.assertLess(strat.stderr, crude.stderr)
        true = (math.e - 1) ** 2
        self.assertAlmostEqual(strat.estimate, true, delta=5 * strat.stderr)

    def test_rejects_too_few_samples(self):
        with self.assertRaises(ValueError):
            stratified_mc(lambda x: 1.0, dim=3, n=10, rng=random.Random(0), strata=4)


class TestImportanceSampling(unittest.TestCase):
    def test_uniform_proposal_equals_crude(self):
        f = lambda x: x[0] ** 2
        res = importance_sampling(f, 1, 100_000, random.Random(9),
                                  lambda rng: [rng.random()], lambda x: 1.0)
        self.assertAlmostEqual(res.estimate, 1.0 / 3.0, delta=5 * res.stderr)

    def test_spike_recovered(self):
        dim, sigma = 3, 0.02
        f = lambda x: math.exp(-sum((xi - 0.5) ** 2 for xi in x) / (2 * sigma * sigma))
        one = sigma * math.sqrt(math.pi / 2) * (
            math.erf(0.5 / (sigma * math.sqrt(2))) - math.erf(-0.5 / (sigma * math.sqrt(2))))
        true = one ** dim
        norm = 1.0 / (sigma * math.sqrt(2 * math.pi))
        res = importance_sampling(
            f, dim, 200_000, random.Random(11),
            lambda rng: [rng.gauss(0.5, sigma) for _ in range(dim)],
            lambda x: math.prod(norm * math.exp(-((xi - 0.5) ** 2) / (2 * sigma * sigma))
                                for xi in x))
        self.assertAlmostEqual(res.estimate, true, delta=5 * res.stderr)


class TestControlVariate(unittest.TestCase):
    def test_reduces_variance(self):
        f = lambda x: math.exp(sum(x))
        g = lambda x: 1.0 + sum(x)
        n = 50_000
        crude = crude_mc(f, 3, n, random.Random(21))
        cv = control_variate(f, g, 1.0 + 3 * 0.5, 3, n, random.Random(21))
        self.assertLess(cv.stderr, crude.stderr * 0.5)
        self.assertAlmostEqual(cv.estimate, (math.e - 1) ** 3, delta=5 * cv.stderr)

    def test_exact_for_control_itself(self):
        # f == g  =>  estimator should return the known integral exactly
        g = lambda x: 1.0 + sum(x)
        res = control_variate(g, g, 2.0, 2, 5000, random.Random(0))
        self.assertAlmostEqual(res.estimate, 2.0, places=10)


class TestCoverage(unittest.TestCase):
    def test_95pct_ci_coverage(self):
        # estimates should fall inside estimate +- 1.96*se about 95% of the time
        f = lambda x: math.exp(sum(x))
        true = (math.e - 1) ** 2
        hits, reps = 0, 200
        for r in range(reps):
            res = crude_mc(f, 2, 4000, random.Random(50_000 + r))
            lo, hi = res.ci()
            hits += (lo <= true <= hi)
        cov = hits / reps
        self.assertGreater(cov, 0.88)
        self.assertLess(cov, 1.0)


class TestEdgeCases(unittest.TestCase):
    def test_almost_everywhere_zero_with_importance(self):
        dim, side = 4, 0.05
        f = lambda x: 1.0 if all(xi <= side for xi in x) else 0.0
        true = side ** dim
        res = importance_sampling(
            f, dim, 20_000, random.Random(31),
            lambda rng: [rng.uniform(0, side) for _ in range(dim)],
            lambda x: 1.0 / true if all(0.0 <= xi <= side for xi in x) else 0.0)
        self.assertAlmostEqual(res.estimate, true, delta=5 * res.stderr)

    def test_degenerate_slab_stratified(self):
        dim, eps = 4, 1e-3
        f = lambda x: 1.0 if x[0] <= eps else 0.0
        res = stratified_mc(f, dim, 40_000, random.Random(33),
                            strata=[2000, 1, 1, 1])
        self.assertAlmostEqual(res.estimate, eps, delta=5 * res.stderr)


if __name__ == "__main__":
    unittest.main()
