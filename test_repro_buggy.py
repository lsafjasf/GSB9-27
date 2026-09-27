"""Deterministic reproductions of the idle-reaper mis-kill (buggy pool).

Every test here PASSES against pool_buggy.py, which means the bug is
present: each one ends with a borrowed connection that the reaper closed.
The same scenarios are asserted to be SAFE against pool_fixed.py in
test_pool_fixed.py.

Run:  python3 -m unittest test_repro_buggy -v
"""

import unittest

from pool_buggy import BuggyConnectionPool, ConnectionClosedError


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def execute(self, sql="SELECT 1"):
        if self.closed:
            raise ConnectionClosedError("connection is closed")
        return "ok"


def make_pool(idle_timeout=30.0):
    clock = FakeClock()
    pool = BuggyConnectionPool(FakeConnection, idle_timeout=idle_timeout,
                               clock=clock)
    return pool, clock


class ReproMisKillTest(unittest.TestCase):
    def test_repro_1_long_borrow_killed_while_in_use(self):
        """Borrowed and never returned, but reaper closes it by borrow age."""
        pool, clock = make_pool()
        conn = pool.borrow()
        clock.advance(31.0)          # business logic still using `conn`
        pool.reap_once()
        # BUG: the borrowed connection was closed underneath the borrower.
        self.assertTrue(conn.closed)
        with self.assertRaises(ConnectionClosedError):
            conn.execute()

    def test_repro_2_reap_races_give_back(self):
        """Reaper scans while conn is borrowed; give_back lands before close."""
        pool, clock = make_pool()
        conn = pool.borrow()
        clock.advance(31.0)
        victims = pool._scan()       # reaper thread gets this far...
        pool.give_back(conn)         # ...meanwhile the borrower returns it
        pool._close(victims)         # ...and the reaper kills it anyway
        # BUG: the connection was legitimately idle for 0 seconds, yet closed.
        self.assertTrue(conn.closed)
        recycled = pool.borrow()     # next borrower may even get a fresh one,
        self.assertIsNot(recycled, conn)  # but the old one died for no reason

    def test_repro_3_just_borrowed_killed_by_scan(self):
        """Idle conn picked as victim, borrowed in the scan/close window."""
        pool, clock = make_pool()
        conn = pool.borrow()
        pool.give_back(conn)
        clock.advance(31.0)          # conn is now genuinely idle-expired
        victims = pool._scan()       # reaper selects it...
        borrowed = pool.borrow()     # ...but a borrower checks it out first
        self.assertIs(borrowed, conn)
        pool._close(victims)         # ...and the reaper closes it regardless
        # BUG: the just-borrowed connection is dead in the borrower's hands.
        self.assertTrue(borrowed.closed)
        with self.assertRaises(ConnectionClosedError):
            borrowed.execute()


if __name__ == "__main__":
    unittest.main()
