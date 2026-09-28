# GSB9-27

Adaptive-step 4th-order ODE solver (RKF45, embedded 4/5 Fehlberg pair),
Python 3 standard library only.

## Files

- `adaptive_rk.py` — solver library (`solve(f, t0, t1, y0, ...)`)
- `test_adaptive_rk.py` — self-tests vs analytic solutions + edge cases
- `demo.py` — benchmark run; writes error data and step logs to `results/`
- `results/` — `summary.csv` (global errors) and `steps_*.csv` (per-step h and error estimates)

## Run

```sh
python3 test_adaptive_rk.py -v   # self-tests / analytic comparison
python3 demo.py                  # regenerate error data + step sequences
```

## Usage

```python
from adaptive_rk import solve

sol = solve(lambda t, y: -y, 0.0, 5.0, 1.0, atol=1e-10, rtol=1e-9)
if sol.ok:
    print(sol.t[-1], sol.y[-1])
for s in sol.steps:          # every attempted step
    print(s.t, s.h, s.err, s.accepted)
```

Failures are reported via `sol.status == "failed"`, a machine-readable
`sol.reason`, and `sol.message` (which includes the location plus the
step size and error estimate at the failure point). Reasons:

- `diverged` — the solution blows up (super-exponential growth /
  non-finite values); loosening the tolerance will not help
- `non_smooth_rhs` — the right-hand side is not differentiable there
  (error estimate stops shrinking with `h`, or `f` went non-finite)
- `tolerance` — the local error genuinely cannot be met with `|h| >= h_min`
- `min_step` — step size underflowed below `h_min`
- `max_steps` — step budget exhausted
