#!/usr/bin/env python3
"""CLI wrapper around supervisor.Supervisor.

Usage:
    python3 supervise.py [options] -- <command> [args...]

While the circuit is open, type "reset" + ENTER (or send SIGUSR1) to
resume auto-restarts. SIGTERM/SIGINT are forwarded to the child.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from supervisor import Supervisor


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-delay", type=float, default=1.0)
    parser.add_argument("--max-delay", type=float, default=30.0)
    parser.add_argument("--fast-crash-window", type=float, default=2.0)
    parser.add_argument("--fast-crash-threshold", type=int, default=3)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("no command given (use -- <command> [args...])")

    sup = Supervisor(
        command,
        base_delay=args.base_delay,
        max_delay=args.max_delay,
        fast_crash_window=args.fast_crash_window,
        fast_crash_threshold=args.fast_crash_threshold,
    )

    def on_signal(signum, _frame):
        if signum == signal.SIGUSR1:
            sup.reset()
        else:
            sup.stop()

    signal.signal(signal.SIGUSR1, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    def stdin_watcher():
        for line in sys.stdin:
            if line.strip() == "reset":
                sup.reset()

    threading.Thread(target=stdin_watcher, daemon=True).start()

    last = sup.run()
    return 0 if (last and last.normal) else 1


if __name__ == "__main__":
    sys.exit(main())
