"""CLI runner used by the regression tests to execute one action in a
separate process (so it can be killed for real).

Usage:
    python3 runner.py <buggy|fixed> <workdir> <session_id> <action_id> \
        [--crash-after STEP] [--kill]

--crash-after STEP  invoke the crash hook after STEP's side effect but
                    before the state write (the worst crash point).
--kill              die via SIGKILL instead of raising SimulatedCrash.
"""

import argparse
import os
import signal
import sys

from session_state import SessionEngine, SimulatedCrash
from buggy_session import BuggySessionEngine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("engine", choices=("buggy", "fixed"))
    parser.add_argument("workdir")
    parser.add_argument("session_id")
    parser.add_argument("action_id")
    parser.add_argument("--crash-after", metavar="STEP", default=None)
    parser.add_argument("--kill", action="store_true",
                        help="use real SIGKILL instead of SimulatedCrash")
    args = parser.parse_args()

    crash_hook = None
    if args.crash_after is not None:
        def crash_hook(step):
            if step == args.crash_after:
                if args.kill:
                    os.kill(os.getpid(), signal.SIGKILL)
                raise SimulatedCrash(step)

    cls = BuggySessionEngine if args.engine == "buggy" else SessionEngine
    engine = cls(args.workdir, args.session_id)
    engine.run_action(args.action_id, crash_hook=crash_hook)
    print("completed")


if __name__ == "__main__":
    sys.exit(main())
