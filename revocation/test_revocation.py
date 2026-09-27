"""Self-tests for revocation.RevocationList. Run: python3 -m unittest -v"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from revocation import RevocationList, Verdict


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt

    def rollback(self, dt):
        self.t -= dt


class RevocationTestBase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "revocations.json")
        self.rl = RevocationList(path=self.path, now=self.clock)

    def tearDown(self):
        self.tmp.cleanup()


class TestVerdicts(RevocationTestBase):
    def test_not_revoked(self):
        self.assertIs(self.rl.check("a", exp=self.clock.t + 100), Verdict.NOT_REVOKED)

    def test_revoked(self):
        self.rl.revoke("a", exp=self.clock.t + 100)
        self.assertIs(self.rl.check("a", exp=self.clock.t + 100), Verdict.REVOKED)

    def test_unknown_after_record_purged(self):
        exp = self.clock.t + 100
        self.rl.revoke("a", exp=exp)
        self.clock.advance(200)  # token naturally expired
        self.assertEqual(self.rl.purge(), 1)
        # Record gone, token lifetime over -> cannot determine.
        self.assertIs(self.rl.check("a", exp=exp), Verdict.UNKNOWN)

    def test_unknown_for_expired_token_never_revoked(self):
        exp = self.clock.t - 1
        self.assertIs(self.rl.check("ghost", exp=exp), Verdict.UNKNOWN)

    def test_revoked_record_still_live_until_expiry(self):
        exp = self.clock.t + 100
        self.rl.revoke("a", exp=exp)
        self.clock.advance(99)
        self.assertIs(self.rl.check("a", exp=exp), Verdict.REVOKED)


class TestPersistence(RevocationTestBase):
    def test_restart_consistency(self):
        """Verdicts before save must equal verdicts after reload."""
        t = self.clock.t
        self.rl.revoke("revoked-1", exp=t + 500)
        self.rl.revoke("revoked-2", exp=t + 900)
        self.rl.revoke("expires-soon", exp=t + 50)
        self.rl.save()

        cases = [
            ("revoked-1", t + 500),
            ("revoked-2", t + 900),
            ("expires-soon", t + 50),
            ("never-revoked", t + 500),
            ("old-token", t - 10),  # already expired -> UNKNOWN
        ]
        before = [(j, e, self.rl.check(j, e)) for j, e in cases]

        # Simulate restart: brand-new instance, same clock, same file.
        rl2 = RevocationList(path=self.path, now=self.clock)
        after = [(j, e, rl2.check(j, e)) for j, e in cases]

        self.assertEqual(before, after)
        self.assertEqual(self.rl.snapshot(), rl2.snapshot())

    def test_restart_consistency_after_purge(self):
        t = self.clock.t
        self.rl.revoke("short", exp=t + 10)
        self.rl.revoke("long", exp=t + 10_000)
        self.clock.advance(100)  # 'short' now expired
        self.rl.purge()
        self.rl.save()
        before = [(j, e, self.rl.check(j, e)) for j, e in
                  [("short", t + 10), ("long", t + 10_000), ("other", t + 5_000)]]
        rl2 = RevocationList(path=self.path, now=self.clock)
        after = [(j, e, rl2.check(j, e)) for j, e in
                 [("short", t + 10), ("long", t + 10_000), ("other", t + 5_000)]]
        self.assertEqual(before, after)

    def test_save_is_atomic_no_tmp_left(self):
        self.rl.revoke("a", exp=self.clock.t + 1)
        self.rl.save()
        leftovers = [f for f in os.listdir(self.tmp.name) if f.startswith(".revoke-")]
        self.assertEqual(leftovers, [])

    def test_load_missing_file_ok(self):
        rl = RevocationList(path=os.path.join(self.tmp.name, "nope.json"),
                            now=self.clock)
        self.assertEqual(len(rl), 0)


class TestBoundsAndPurge(RevocationTestBase):
    def test_purge_bounds_memory(self):
        t = self.clock.t
        self.rl.revoke_many((f"tok-{i}", t + 60) for i in range(10_000))
        self.rl.revoke("survivor", exp=t + 10_000)
        before = self.rl.memory_stats()
        self.clock.advance(61)
        removed = self.rl.purge()
        after = self.rl.memory_stats()
        self.assertEqual(removed, 10_000)
        self.assertEqual(after["records"], 1)
        self.assertLess(after["approx_bytes"], before["approx_bytes"])
        print(f"\n[memory] before purge: {before} | after purge: {after}")

    def test_store_does_not_grow_unbounded(self):
        t = self.clock.t
        for round_ in range(5):
            self.rl.revoke_many((f"r{round_}-{i}", t + 10) for i in range(1000))
            self.clock.advance(11)
            self.rl.purge()
        self.assertEqual(len(self.rl), 0)


class TestEdgeCases(RevocationTestBase):
    def test_batch_revoke(self):
        t = self.clock.t
        changed = self.rl.revoke_many((f"b{i}", t + 100) for i in range(500))
        self.assertEqual(changed, 500)
        self.assertEqual(len(self.rl), 500)
        self.assertIs(self.rl.check("b250", exp=t + 100), Verdict.REVOKED)

    def test_duplicate_revoke_idempotent(self):
        t = self.clock.t
        self.assertTrue(self.rl.revoke("dup", exp=t + 100))
        self.assertFalse(self.rl.revoke("dup", exp=t + 100))  # same -> no change
        self.assertFalse(self.rl.revoke("dup", exp=t + 50))   # shorter -> ignored
        self.assertTrue(self.rl.revoke("dup", exp=t + 200))   # longer -> wins
        self.assertEqual(len(self.rl), 1)
        self.assertEqual(self.rl.snapshot()["dup"], t + 200)

    def test_revoke_already_expired_token(self):
        t = self.clock.t
        self.rl.revoke("dead", exp=t - 1)  # already expired
        # Record is immediately purgeable; verdict for expired token is UNKNOWN.
        self.assertIs(self.rl.check("dead", exp=t - 1), Verdict.UNKNOWN)
        self.assertEqual(self.rl.purge(), 1)
        self.assertEqual(len(self.rl), 0)

    def test_clock_rollback_keeps_records(self):
        t = self.clock.t
        self.rl.revoke("a", exp=t + 100)
        self.clock.advance(150)          # now past expiry
        self.clock.rollback(200)         # clock jumps back before expiry
        self.assertEqual(self.rl.purge(), 0)  # nothing wrongly purged
        self.assertIs(self.rl.check("a", exp=t + 100), Verdict.REVOKED)

    def test_clock_rollback_purge_conservative(self):
        t = self.clock.t
        self.rl.revoke("a", exp=t + 100)
        self.clock.advance(500)
        self.assertEqual(self.rl.purge(), 1)   # expired at advanced clock
        self.rl.revoke("b", exp=t + 100)
        self.clock.rollback(1000)              # big rollback
        self.assertEqual(self.rl.purge(), 0)   # rollback never resurrects/deletes wrongly
        self.assertIs(self.rl.check("b", exp=t + 100), Verdict.REVOKED)

    def test_boundary_expiry_equals_now(self):
        t = self.clock.t
        self.rl.revoke("edge", exp=t)
        # exp == now counts as expired (record no longer live).
        self.assertIs(self.rl.check("edge", exp=t), Verdict.UNKNOWN)
        self.assertEqual(self.rl.purge(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
