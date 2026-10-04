"""volume_refcount 的自测（标准库 unittest，可注入时钟，无需真实等待）。

覆盖：单使用者、多使用者、异常退出（租约超时回收）、重复挂载，
以及删除保护、非法卸载、过期续期等边界用例。

运行：python3 test_volume_refcount.py -v
"""

import unittest

from volume_refcount import (
    LeaseExpired,
    NotMounted,
    VolumeBusy,
    VolumeError,
    VolumeManager,
    VolumeNotFound,
)


class FakeClock:
    """可手动推进的时钟，让租约超时完全确定。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


class VolumeManagerTestBase(unittest.TestCase):
    TTL = 30.0

    def setUp(self) -> None:
        self.clock = FakeClock()
        self.mgr = VolumeManager(lease_ttl=self.TTL, clock=self.clock)
        self.mgr.create_volume("vol-1")


class TestSingleUser(VolumeManagerTestBase):
    """情形一：单使用者挂载/卸载，计数 0 -> 1 -> 0。"""

    def test_mount_unmount_count_sequence(self):
        self.assertEqual(self.mgr.refcount("vol-1"), 0)

        rec = self.mgr.mount("vol-1", "pod-a")
        self.assertEqual(rec.refcount, 1)
        self.assertFalse(rec.duplicate)
        self.assertEqual(self.mgr.refcount("vol-1"), 1)

        after = self.mgr.unmount("vol-1", "pod-a")
        self.assertEqual(after, 0)
        self.assertEqual(self.mgr.refcount("vol-1"), 0)

    def test_delete_allowed_only_at_zero(self):
        self.mgr.mount("vol-1", "pod-a")
        with self.assertRaises(VolumeBusy):
            self.mgr.delete_volume("vol-1")
        self.mgr.unmount("vol-1", "pod-a")
        self.mgr.delete_volume("vol-1")
        self.assertFalse(self.mgr.exists("vol-1"))


class TestMultiUser(VolumeManagerTestBase):
    """情形二：多使用者并发挂载，计数逐一累加/递减，互不影响。"""

    def test_counts_track_each_holder(self):
        expected = []
        for i, holder in enumerate(["pod-a", "pod-b", "pod-c"], start=1):
            rec = self.mgr.mount("vol-1", holder)
            expected.append((holder, rec.refcount))
        self.assertEqual(
            expected, [("pod-a", 1), ("pod-b", 2), ("pod-c", 3)]
        )
        self.assertEqual(self.mgr.holders("vol-1"), ["pod-a", "pod-b", "pod-c"])

        self.assertEqual(self.mgr.unmount("vol-1", "pod-b"), 2)
        self.assertEqual(self.mgr.unmount("vol-1", "pod-a"), 1)
        self.assertEqual(self.mgr.unmount("vol-1", "pod-c"), 0)

    def test_one_holder_crash_does_not_affect_others(self):
        self.mgr.mount("vol-1", "pod-a")
        self.mgr.mount("vol-1", "pod-b")
        # pod-a 正常续期，pod-b 异常退出不再续期
        self.clock.advance(20)
        self.mgr.renew("vol-1", "pod-a")
        self.clock.advance(15)  # t=35：pod-b 的租约(t=30 到期)已超时

        reclaimed = self.mgr.reap_expired()
        self.assertEqual(len(reclaimed), 1)
        self.assertEqual(reclaimed[0].holder_id, "pod-b")
        self.assertEqual(reclaimed[0].refcount_after, 1)
        self.assertEqual(self.mgr.holders("vol-1"), ["pod-a"])


class TestAbnormalExitAndLease(VolumeManagerTestBase):
    """情形三：使用者异常退出 -> 租约超时 -> 回收器回收，计数归零后才可删除。"""

    def test_crash_reclaimed_by_lease_timeout(self):
        self.mgr.mount("vol-1", "pod-a")
        # pod-a 崩溃，没有 unmount 也没有 renew
        self.clock.advance(self.TTL + 0.1)

        reclaimed = self.mgr.reap_expired()
        self.assertEqual(len(reclaimed), 1)
        self.assertEqual(reclaimed[0].holder_id, "pod-a")
        self.assertEqual(reclaimed[0].refcount_after, 0)
        self.assertEqual(self.mgr.refcount("vol-1"), 0)
        self.mgr.delete_volume("vol-1")  # 回收完成后允许删除

    def test_renewal_extends_lease_and_prevents_reclaim(self):
        self.mgr.mount("vol-1", "pod-a")          # t=1000，租约至 1030
        self.clock.advance(20)                     # t=1020
        deadline = self.mgr.renew("vol-1", "pod-a")  # 续期至 1050
        self.assertEqual(deadline, 1020 + self.TTL)

        self.clock.advance(29)                     # t=1049：未超时
        self.assertEqual(self.mgr.reap_expired(), [])
        self.assertEqual(self.mgr.refcount("vol-1"), 1)

        self.clock.advance(1.1)                    # t=1050.1：超时
        reclaimed = self.mgr.reap_expired()
        self.assertEqual([r.holder_id for r in reclaimed], ["pod-a"])
        self.assertEqual(self.mgr.refcount("vol-1"), 0)

    def test_renew_after_expiry_rejected(self):
        self.mgr.mount("vol-1", "pod-a")
        self.clock.advance(self.TTL + 1)
        with self.assertRaises(LeaseExpired):
            self.mgr.renew("vol-1", "pod-a")
        # 重新挂载可以恢复，计数从 0 重新计为 1
        rec = self.mgr.mount("vol-1", "pod-a")
        self.assertEqual(rec.refcount, 1)
        self.assertFalse(rec.duplicate)

    def test_unmount_after_reclaim_raises(self):
        self.mgr.mount("vol-1", "pod-a")
        self.clock.advance(self.TTL + 1)
        self.mgr.reap_expired()
        with self.assertRaises(NotMounted):
            self.mgr.unmount("vol-1", "pod-a")


class TestDuplicateMount(VolumeManagerTestBase):
    """情形四：同一使用者重复挂载同一卷，计数不得错乱。"""

    def test_duplicate_mount_is_idempotent(self):
        first = self.mgr.mount("vol-1", "pod-a")
        second = self.mgr.mount("vol-1", "pod-a")
        third = self.mgr.mount("vol-1", "pod-a")

        self.assertEqual((first.refcount, first.duplicate), (1, False))
        self.assertEqual((second.refcount, second.duplicate), (1, True))
        self.assertEqual((third.refcount, third.duplicate), (1, True))
        self.assertEqual(self.mgr.refcount("vol-1"), 1)

        # 只需一次卸载即归零
        self.assertEqual(self.mgr.unmount("vol-1", "pod-a"), 0)

    def test_duplicate_mount_refreshes_lease(self):
        self.mgr.mount("vol-1", "pod-a")           # 租约至 1030
        self.clock.advance(25)                     # t=1025
        rec = self.mgr.mount("vol-1", "pod-a")     # 重复挂载 -> 续期至 1055
        self.assertTrue(rec.duplicate)
        self.assertEqual(rec.lease_deadline, 1025 + self.TTL)

        self.clock.advance(20)                     # t=1045：旧租约已过期但新租约有效
        self.assertEqual(self.mgr.reap_expired(), [])
        self.assertEqual(self.mgr.refcount("vol-1"), 1)

    def test_duplicate_mount_does_not_shadow_other_holders(self):
        self.mgr.mount("vol-1", "pod-a")
        self.mgr.mount("vol-1", "pod-b")
        self.mgr.mount("vol-1", "pod-a")           # 重复挂载
        self.assertEqual(self.mgr.refcount("vol-1"), 2)
        self.assertEqual(self.mgr.unmount("vol-1", "pod-a"), 1)
        self.assertEqual(self.mgr.holders("vol-1"), ["pod-b"])


class TestEdgeCases(VolumeManagerTestBase):
    """边界用例：非法参数与非法时序一律显式报错，不产生静默计数漂移。"""

    def test_mount_unknown_volume(self):
        with self.assertRaises(VolumeNotFound):
            self.mgr.mount("no-such-vol", "pod-a")

    def test_unmount_without_mount(self):
        with self.assertRaises(NotMounted):
            self.mgr.unmount("vol-1", "pod-a")

    def test_double_unmount(self):
        self.mgr.mount("vol-1", "pod-a")
        self.mgr.unmount("vol-1", "pod-a")
        with self.assertRaises(NotMounted):
            self.mgr.unmount("vol-1", "pod-a")

    def test_create_duplicate_volume(self):
        with self.assertRaises(VolumeError):
            self.mgr.create_volume("vol-1")

    def test_delete_unknown_volume(self):
        with self.assertRaises(VolumeNotFound):
            self.mgr.delete_volume("no-such-vol")

    def test_empty_ids_rejected(self):
        for bad in ("", None, 123):
            with self.assertRaises(ValueError):
                self.mgr.mount("vol-1", bad)
            with self.assertRaises(ValueError):
                self.mgr.create_volume(bad)

    def test_invalid_lease_ttl(self):
        with self.assertRaises(ValueError):
            VolumeManager(lease_ttl=0)
        with self.assertRaises(ValueError):
            VolumeManager(lease_ttl=-5)

    def test_refcount_query_lazily_reclaims(self):
        # 不显式调用 reap_expired，查询计数时也会惰性回收过期租约
        self.mgr.mount("vol-1", "pod-a")
        self.clock.advance(self.TTL + 1)
        self.assertEqual(self.mgr.refcount("vol-1"), 0)

    def test_multiple_volumes_isolated(self):
        self.mgr.create_volume("vol-2")
        self.mgr.mount("vol-1", "pod-a")
        self.mgr.mount("vol-2", "pod-a")
        self.mgr.mount("vol-2", "pod-b")
        self.assertEqual(self.mgr.refcount("vol-1"), 1)
        self.assertEqual(self.mgr.refcount("vol-2"), 2)
        self.mgr.unmount("vol-2", "pod-a")
        self.assertEqual(self.mgr.refcount("vol-1"), 1)
        self.assertEqual(self.mgr.refcount("vol-2"), 1)


if __name__ == "__main__":
    unittest.main()
