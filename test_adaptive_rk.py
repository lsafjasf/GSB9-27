"""Self-tests for adaptive_rk: comparisons against analytic solutions and
edge cases (zero initial value, backward integration, discontinuous
right-hand side at the endpoint, minimum-step failure reporting).

Run:  python3 test_adaptive_rk.py -v
"""

import math
import unittest

from adaptive_rk import solve


class TestAnalyticAgreement(unittest.TestCase):
    """Actual global error must stay within the requested tolerance class."""

    def test_exponential_decay(self):
        # y' = -y, y(0) = 1  ->  y(t) = e^{-t}
        sol = solve(lambda t, y: -y, 0.0, 5.0, 1.0, atol=1e-10, rtol=1e-9)
        self.assertTrue(sol.ok, sol.message)
        exact = math.exp(-5.0)
        err = abs(sol.y[-1] - exact)
        print("\n[decay]      y(5)=%.15e exact=%.15e err=%.3e steps=%d"
              % (sol.y[-1], exact, err, len(sol.accepted_steps)))
        self.assertLess(err, 1e-7)

    def test_harmonic_oscillator(self):
        # y1' = y2, y2' = -y1, y(0) = (1, 0)  ->  (cos t, -sin t)
        sol = solve(lambda t, y: [y[1], -y[0]], 0.0, 2.0 * math.pi,
                    [1.0, 0.0], atol=1e-11, rtol=1e-9)
        self.assertTrue(sol.ok, sol.message)
        err = math.hypot(sol.y[-1][0] - 1.0, sol.y[-1][1] - 0.0)
        print("\n[oscillator] y(2pi)=(%.15e, %.15e) err=%.3e steps=%d"
              % (sol.y[-1][0], sol.y[-1][1], err, len(sol.accepted_steps)))
        self.assertLess(err, 1e-6)

    def test_stiff_system(self):
        # Eigenvalues -1 and -100.  y(0) = (2, 0) ->
        # y(t) = (e^{-t} + e^{-100t}, e^{-t} - e^{-100t})
        def f(t, y):
            return [-50.5 * y[0] + 49.5 * y[1],
                    49.5 * y[0] - 50.5 * y[1]]
        sol = solve(f, 0.0, 1.0, [2.0, 0.0], atol=1e-10, rtol=1e-8)
        self.assertTrue(sol.ok, sol.message)
        e1, e100 = math.exp(-1.0), math.exp(-100.0)
        exact = (e1 + e100, e1 - e100)
        err = max(abs(sol.y[-1][0] - exact[0]),
                  abs(sol.y[-1][1] - exact[1]))
        print("\n[stiff]      y(1)=(%.15e, %.15e) err=%.3e steps=%d"
              % (sol.y[-1][0], sol.y[-1][1], err, len(sol.accepted_steps)))
        self.assertLess(err, 1e-5)


class TestEdgeCases(unittest.TestCase):
    def test_zero_initial_value(self):
        # y' = cos(t), y(0) = 0  ->  y(t) = sin(t)
        sol = solve(lambda t, y: math.cos(t), 0.0, math.pi, 0.0,
                    atol=1e-10, rtol=1e-9)
        self.assertTrue(sol.ok, sol.message)
        err = abs(sol.y[-1] - math.sin(math.pi))
        print("\n[zero-ic]    y(pi)=%.15e err=%.3e" % (sol.y[-1], err))
        self.assertLess(err, 1e-8)
        # Identically zero solution must not trip the error controller.
        sol0 = solve(lambda t, y: -y, 0.0, 1.0, 0.0)
        self.assertTrue(sol0.ok, sol0.message)
        self.assertEqual(sol0.y[-1], 0.0)

    def test_backward_integration(self):
        # Integrate y' = -y from t=0 backwards to t=-3: y(-3) = e^{3}.
        sol = solve(lambda t, y: -y, 0.0, -3.0, 1.0,
                    atol=1e-10, rtol=1e-9)
        self.assertTrue(sol.ok, sol.message)
        exact = math.exp(3.0)
        err = abs(sol.y[-1] - exact)
        hs = [s.h for s in sol.accepted_steps]
        print("\n[backward]   y(-3)=%.15e exact=%.15e err=%.3e"
              % (sol.y[-1], exact, err))
        self.assertLess(err, 1e-6)
        self.assertTrue(all(h < 0.0 for h in hs))
        # Oscillator backwards over a full period returns to the start.
        sol2 = solve(lambda t, y: [y[1], -y[0]], 0.0, -2.0 * math.pi,
                     [1.0, 0.0], atol=1e-11, rtol=1e-9)
        self.assertTrue(sol2.ok, sol2.message)
        self.assertLess(math.hypot(sol2.y[-1][0] - 1.0, sol2.y[-1][1]), 1e-6)

    def test_discontinuous_rhs_at_endpoint(self):
        # y' = 1 for t < 1, y' = 0 for t >= 1 (jump at the endpoint t=1).
        # Analytic limit from the left: y(1) = 1.
        sol = solve(lambda t, y: 1.0 if t < 1.0 else 0.0,
                    0.0, 1.0, 0.0, atol=1e-10, rtol=1e-9)
        self.assertTrue(sol.ok, sol.message)
        err = abs(sol.y[-1] - 1.0)
        n_rej = len(sol.rejected_steps)
        print("\n[discont]    y(1)=%.15e err=%.3e rejected=%d"
              % (sol.y[-1], err, n_rej))
        self.assertLess(err, 1e-6)
        self.assertEqual(sol.t[-1], 1.0)

    def test_blowup_failure_is_reported_as_divergence(self):
        # y' = y^2, y(0) = 1 blows up at t = 1; the solver must fail near
        # t = 1 instead of spinning with steps below h_min, and the cause
        # is the diverging solution -- not an unsatisfiable tolerance.
        sol = solve(lambda t, y: y * y, 0.0, 2.0, 1.0,
                    atol=1e-10, rtol=1e-8, h_min=1e-10)
        print("\n[blowup]     status=%s reason=%s t_fail=%.6f msg=%s"
              % (sol.status, sol.reason, sol.t[-1], sol.message))
        self.assertFalse(sol.ok)
        self.assertEqual(sol.status, "failed")
        self.assertEqual(sol.reason, "diverged")
        self.assertIn("diverg", sol.message)
        self.assertIn("t =", sol.message)
        self.assertGreater(sol.t[-1], 0.9)
        self.assertLess(sol.t[-1], 1.1)
        # No step smaller than h_min was ever taken.
        self.assertTrue(all(abs(s.h) >= 1e-10 for s in sol.accepted_steps))

    def test_tolerance_failure_reason(self):
        # Smooth problem, impossibly tight tolerances: the local error
        # genuinely cannot be met with |h| >= h_min.
        sol = solve(lambda t, y: -y, 0.0, 1.0, 1.0,
                    atol=1e-30, rtol=1e-30, h_min=1e-6)
        print("\n[tolerance]  status=%s reason=%s msg=%s"
              % (sol.status, sol.reason, sol.message))
        self.assertFalse(sol.ok)
        self.assertEqual(sol.reason, "tolerance")
        self.assertIn("t =", sol.message)
        self.assertIn("h_min", sol.message)

    def test_non_smooth_rhs_failure_reason(self):
        # y' jumps at t = 0.5; with h_min too large to resolve the kink
        # the error estimate stops shrinking with h.
        sol = solve(lambda t, y: 1.0 if t < 0.5 else -1.0,
                    0.0, 1.0, 0.0, atol=1e-10, rtol=1e-9, h_min=1e-4)
        print("\n[non-smooth] status=%s reason=%s msg=%s"
              % (sol.status, sol.reason, sol.message))
        self.assertFalse(sol.ok)
        self.assertEqual(sol.reason, "non_smooth_rhs")
        self.assertIn("non-differentiable", sol.message)
        self.assertIn("t =", sol.message)

    def test_step_log_records_h_and_error(self):
        sol = solve(lambda t, y: -y, 0.0, 1.0, 1.0)
        self.assertTrue(sol.ok, sol.message)
        self.assertEqual(len(sol.steps) > 0, True)
        for s in sol.steps:
            self.assertTrue(math.isfinite(s.h))
            self.assertTrue(math.isfinite(s.err))
            self.assertGreaterEqual(s.err, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
