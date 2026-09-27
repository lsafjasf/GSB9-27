"""Self-tests for rk45: analytic-solution cross-checks and edge cases.

Run:  python3 -m unittest -v        (or: python3 test_rk45.py -v)
"""

import math
import statistics
import unittest

from rk45 import IntegrationFailure, integrate

# Global error may exceed the per-step tolerance by an accumulation factor;
# the margins below are generous upper bounds, actual errors are printed
# by demo.py and are typically 1-2 orders of magnitude smaller.
MARGIN = 50.0


class TestAnalyticCrossCheck(unittest.TestCase):
    """Actual global error must stay within the requested tolerance."""

    def test_exponential_decay(self):
        # y' = -y, y(0) = 1  ->  y = e^{-t}
        rtol, atol = 1e-8, 1e-11
        sol = integrate(lambda t, y: [-y[0]], 0.0, 1.0, 5.0,
                        rtol=rtol, atol=atol)
        for t, (y,) in zip(sol.t, sol.y):
            exact = math.exp(-t)
            self.assertLessEqual(abs(y - exact),
                                 MARGIN * (atol + rtol * abs(exact)))
        err_end = abs(sol.y[-1][0] - math.exp(-5.0))
        self.assertLess(err_end, MARGIN * rtol * math.exp(-5.0))

    def test_harmonic_oscillator(self):
        # y1' = y2, y2' = -y1, y(0) = (1, 0) -> (cos t, -sin t)
        rtol, atol = 1e-8, 1e-11
        sol = integrate(lambda t, y: [y[1], -y[0]], 0.0, [1.0, 0.0], 10.0,
                        rtol=rtol, atol=atol)
        for t, (y1, y2) in zip(sol.t, sol.y):
            self.assertLessEqual(abs(y1 - math.cos(t)),
                                 MARGIN * (atol + rtol))
            self.assertLessEqual(abs(y2 + math.sin(t)),
                                 MARGIN * (atol + rtol))

    def test_stiff_system(self):
        # Eigenvalues -1 and -1000; exact solution known in closed form.
        # u' = 998u + 1998v, v' = -999u - 1999v
        # u(0)=1, v(0)=0 -> u = 2e^{-t} - e^{-1000t}, v = -e^{-t} + e^{-1000t}
        def f(t, y):
            u, v = y
            return [998.0 * u + 1998.0 * v, -999.0 * u - 1999.0 * v]

        rtol, atol = 1e-6, 1e-10
        t1 = 0.1
        sol = integrate(f, 0.0, [1.0, 0.0], t1, rtol=rtol, atol=atol)
        for t, (u, v) in zip(sol.t, sol.y):
            ue = 2.0 * math.exp(-t) - math.exp(-1000.0 * t)
            ve = -math.exp(-t) + math.exp(-1000.0 * t)
            self.assertLessEqual(abs(u - ue), MARGIN * (atol + rtol * abs(ue)))
            self.assertLessEqual(abs(v - ve), MARGIN * (atol + rtol * abs(ve)))
        # Explicit RK on a stiff problem must be forced to small steps:
        # the stability limit is |h*lambda| <= ~2.78, i.e. h <~ 2.78e-3
        # here. Single accepted steps may transiently exceed it before the
        # controller reacts, so check the *sustained* (median) step size.
        hs = [abs(r.h) for r in sol.history if r.accepted]
        self.assertLess(statistics.median(hs), 2.8e-3)
        self.assertGreater(sol.n_accepted, 30)


class TestEdgeCases(unittest.TestCase):
    def test_zero_initial_value_nontrivial(self):
        # y' = cos t, y(0) = 0 -> y = sin t
        sol = integrate(lambda t, y: [math.cos(t)], 0.0, 0.0, 2.0 * math.pi,
                        rtol=1e-9, atol=1e-12)
        for t, (y,) in zip(sol.t, sol.y):
            self.assertLessEqual(abs(y - math.sin(t)),
                                 MARGIN * (1e-12 + 1e-9))

    def test_zero_initial_value_identically_zero(self):
        # y' = y, y(0) = 0 -> y == 0; error estimate is exactly 0 and the
        # controller must take the max-growth branch without producing NaN.
        sol = integrate(lambda t, y: [y[0]], 0.0, 0.0, 1.0)
        for (y,) in sol.y:
            self.assertEqual(y, 0.0)
        for r in sol.history:
            self.assertTrue(math.isfinite(r.err_norm))

    def test_backward_integration(self):
        # Same decay equation integrated backwards: y(2) = e^{-2} -> y(0) = 1.
        rtol, atol = 1e-8, 1e-11
        sol = integrate(lambda t, y: [-y[0]], 2.0, math.exp(-2.0), 0.0,
                        rtol=rtol, atol=atol)
        self.assertLess(sol.t[-1], sol.t[0])  # monotone decreasing output
        for t, (y,) in zip(sol.t, sol.y):
            self.assertLessEqual(abs(y - math.exp(-t)),
                                 MARGIN * (atol + rtol * math.exp(-t)))
        # Step sizes must be negative (signed) on a backward sweep.
        self.assertTrue(all(r.h < 0.0 for r in sol.history))

    def test_endpoint_singular_derivative(self):
        # y' = 1.5*sqrt(t): the equation itself is continuous but its
        # derivative blows up at the start endpoint t = 0 (solution
        # y = t^1.5 has unbounded curvature there). The solver must
        # refine near the endpoint instead of failing or losing accuracy.
        rtol, atol = 1e-8, 1e-11
        sol = integrate(lambda t, y: [1.5 * math.sqrt(t)], 0.0, 0.0, 1.0,
                        rtol=rtol, atol=atol)
        err_end = abs(sol.y[-1][0] - 1.0)
        self.assertLess(err_end, MARGIN * (atol + rtol))
        # First accepted step must be much smaller than the last one:
        # the adaptivity has to react to the curvature blow-up at t = 0.
        hs = [abs(r.h) for r in sol.history if r.accepted]
        self.assertLess(hs[0], 0.1 * hs[-1])

    def test_endpoint_singularity_success_and_failure(self):
        # y' = 1/(2 sqrt(t)) is unbounded at t = 0 (y = sqrt(t)).
        # With a tiny enough h_min the solver grinds through the singular
        # endpoint; with the default h_min it must report failure at
        # t = 0 instead of spinning with absurdly small steps.
        def f(t, y):
            return [0.0 if t == 0.0 else 0.5 / math.sqrt(t)]

        sol = integrate(f, 0.0, 0.0, 1.0, rtol=1e-7, atol=1e-10,
                        h_min=1e-16)
        self.assertLess(abs(sol.y[-1][0] - 1.0), 1e-5)
        hs = [abs(r.h) for r in sol.history if r.accepted]
        self.assertLess(hs[0], 1e-12)  # massive refinement at the endpoint

        with self.assertRaises(IntegrationFailure) as ctx:
            integrate(f, 0.0, 0.0, 1.0, rtol=1e-7, atol=1e-10)
        self.assertEqual(ctx.exception.t, 0.0)  # failure located at endpoint

    def test_endpoint_jump_discontinuity(self):
        # f jumps at the final endpoint t = 1 (f = +1 before, -1 after).
        # The true solution on [0, 1] is y = t; the value at the isolated
        # endpoint does not affect the integral. Accuracy at the jump is
        # only first-order in the last step, so use a loose bound.
        def f(t, y):
            return [1.0 if t < 1.0 else -1.0]

        sol = integrate(f, 0.0, 0.0, 1.0, rtol=1e-8, atol=1e-11)
        self.assertAlmostEqual(sol.t[-1], 1.0)
        self.assertLess(abs(sol.y[-1][0] - 1.0), 0.05)
        # Interior points (away from the jump) keep full accuracy.
        for t, (y,) in zip(sol.t[:-1], sol.y[:-1]):
            self.assertLessEqual(abs(y - t), 1e-6)

    def test_min_step_failure_reports_location(self):
        # Demand an impossible tolerance with a large h_min: the solver
        # must fail loudly and report where, not spin with tiny steps.
        with self.assertRaises(IntegrationFailure) as ctx:
            integrate(lambda t, y: [y[0]], 0.0, 1.0, 1.0,
                      rtol=1e-13, atol=1e-16, h_min=0.2)
        exc = ctx.exception
        self.assertIsNotNone(exc.t)
        self.assertGreaterEqual(exc.t, 0.0)
        self.assertLess(exc.t, 1.0)  # failed partway, reported the spot
        self.assertIn("t=", str(exc))
        self.assertIn("h_min", str(exc))

    def test_step_records_present(self):
        # Every step must expose its step size and error estimate.
        sol = integrate(lambda t, y: [-y[0]], 0.0, 1.0, 1.0)
        self.assertGreater(len(sol.history), 0)
        for r in sol.history:
            self.assertGreater(abs(r.h), 0.0)
            self.assertGreaterEqual(r.err_norm, 0.0)
            self.assertTrue(math.isfinite(r.err_norm))
        self.assertEqual(sol.n_accepted, len(sol.t) - 1)


if __name__ == "__main__":
    unittest.main()
