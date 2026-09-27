#!/usr/bin/env python3
"""Tiny metrics-emitting service used by the tests/demos.

Subcommands:
  once STATE [--incr NAME=N ...] [--mono NAME=N ...]
      open the store, apply updates, flush, print a JSON snapshot, exit.
  daemon STATE --interval SECS
      increment 'requests' and add to 'bytes_sent' every interval seconds,
      flushing after every tick. Stops cleanly on SIGTERM/SIGINT (flush +
      exit). SIGKILL cannot be handled, which is what the crash test relies
      on.
  stop-after daemon also accepts --duration SECS so tests need no signals.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time

from metrics.store import make_store


def _parse_kv(text, cast):
    name, _, value = text.partition("=")
    if not _:
        raise argparse.ArgumentTypeError("expected NAME=VALUE, got %r" % text)
    return name, cast(value)


def cmd_once(args):
    store = make_store(args.state, window_seconds=args.window,
                       on_corrupt=args.on_corrupt,
                       max_age_seconds=args.max_age)
    for name, amount in args.incr:
        store.incr(name, amount)
    for name, amount in args.mono:
        store.add_monotone(name, amount)
    store.flush()
    print(json.dumps(store.snapshot(), sort_keys=True))
    return 0


def cmd_daemon(args):
    store = make_store(args.state, window_seconds=args.window,
                       on_corrupt=args.on_corrupt)
    stop = {"flag": False}

    def handle(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    deadline = None
    if args.duration is not None:
        deadline = time.time() + args.duration
    while not stop["flag"]:
        store.incr("requests", 1)
        store.add_monotone("bytes_sent", 10)
        store.flush()
        if deadline is not None and time.time() >= deadline:
            break
        time.sleep(args.interval)
    store.flush()
    print(json.dumps(store.snapshot(), sort_keys=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="metrics demo service")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("state")
        p.add_argument("--window", type=int, default=3600)
        p.add_argument("--on-corrupt", choices=("recover", "fail", "reset"),
                       default="fail")
        p.add_argument("--max-age", type=int, default=None)

    p_once = sub.add_parser("once")
    add_common(p_once)
    p_once.add_argument("--incr", action="append", default=[],
                        type=lambda s: _parse_kv(s, int))
    p_once.add_argument("--mono", action="append", default=[],
                        type=lambda s: _parse_kv(s, int))
    p_once.set_defaults(func=cmd_once)

    p_daemon = sub.add_parser("daemon")
    add_common(p_daemon)
    p_daemon.add_argument("--interval", type=float, default=1.0)
    p_daemon.add_argument("--duration", type=float, default=None)
    p_daemon.set_defaults(func=cmd_daemon)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
