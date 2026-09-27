"""Monte-Carlo simulation of election latency.

Run:  python3 simulate.py [--trials 2000] [--nodes 5]

Two scenarios per trial:
  * cold-start : all nodes boot as followers, time to first leader.
  * re-elect   : the reigning leader crashes, time to the next leader.

The hard worst-case bound printed below is derived in README.md:
    bound = (F+1)*R + T_min + (n-1)*Delta + 2*d_max
    R     = T_max + 2*d_max          (one failed election round)
"""

import argparse
import math
import statistics

from election import Cluster, Config


def first_leader_time(cluster, since=0.0):
    for t, _nid, text in cluster.timeline:
        if t > since and text.startswith("becomes LEADER"):
            return t
    return None


def trial_cold_start(seed, n, cfg):
    ids = ["N%d" % i for i in range(n)]
    c = Cluster(ids, config=cfg, seed=seed)
    c.run(10.0)
    return first_leader_time(c)


def trial_reelect(seed, n, cfg):
    ids = ["N%d" % i for i in range(n)]
    c = Cluster(ids, config=cfg, seed=seed)
    c.run(1.0)
    leader = c.leader_ids()[0]
    t0 = c.now()
    c.crash(leader)
    c.run(10.0)
    t1 = first_leader_time(c, since=t0)
    return None if t1 is None else t1 - t0


def percentile(data, p):
    data = sorted(data)
    k = int(math.ceil(p / 100.0 * len(data))) - 1
    return data[max(0, min(len(data) - 1, k))]


def report(name, samples, bound=None):
    ms = [s * 1000.0 for s in samples]
    print("%s: n=%d  min=%.1f  mean=%.1f  p50=%.1f  p95=%.1f  p99=%.1f  max=%.1f ms"
          % (name, len(ms), min(ms), statistics.fmean(ms), percentile(ms, 50),
             percentile(ms, 95), percentile(ms, 99), max(ms)))
    if bound is not None:
        ok = "OK" if max(ms) <= bound * 1000.0 else "VIOLATED"
        print("  hard worst-case bound = %.1f ms  ->  %s"
              % (bound * 1000.0, ok))


def worst_case_bound(cfg, n):
    round_time = cfg.election_timeout_max + 2 * cfg.message_delay_max
    return ((cfg.max_consecutive_failures + 1) * round_time
            + cfg.election_timeout_min
            + (n - 1) * cfg.priority_slot
            + 2 * cfg.message_delay_max)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=2000)
    ap.add_argument("--nodes", type=int, default=5)
    args = ap.parse_args()

    cfg = Config()
    bound = worst_case_bound(cfg, args.nodes)
    print("config: T=[%.0f,%.0f]ms heartbeat=%.0fms lease=%.0fms "
          "write_lease=%.1fms delay=[%.1f,%.1f]ms F=%d Delta=%.0fms"
          % (cfg.election_timeout_min * 1e3, cfg.election_timeout_max * 1e3,
             cfg.heartbeat_interval * 1e3, cfg.lease_duration * 1e3,
             cfg.write_lease * 1e3, cfg.message_delay_min * 1e3,
             cfg.message_delay_max * 1e3, cfg.max_consecutive_failures,
             cfg.priority_slot * 1e3))

    cold, reelect = [], []
    for seed in range(args.trials):
        cold.append(trial_cold_start(seed, args.nodes, cfg))
        reelect.append(trial_reelect(1_000_000 + seed, args.nodes, cfg))
    assert all(t is not None for t in cold), "cold-start election failed"
    assert all(t is not None for t in reelect), "re-election failed"

    report("cold-start election", cold)
    report("re-election after leader crash", reelect, bound=bound)


if __name__ == "__main__":
    main()
