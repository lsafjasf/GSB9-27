"""位点持久化测试。"""
import os
import tempfile
import unittest

from changelog import CheckpointStore, Position


class CheckpointStoreTest(unittest.TestCase):
    def test_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CheckpointStore(os.path.join(tmp, "ckpt.json"))
            self.assertIsNone(store.load())

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CheckpointStore(os.path.join(tmp, "ckpt.json"))
            pos = Position(segment="segment-000003.log", offset=4096,
                           last_lsn=128, last_txid=77)
            store.save(pos)
            self.assertEqual(store.load(), pos)

    def test_save_overwrites_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ckpt.json")
            store = CheckpointStore(path)
            store.save(Position(segment="segment-000000.log", offset=10,
                                last_lsn=1))
            store.save(Position(segment="segment-000001.log", offset=20,
                                last_lsn=2))
            self.assertEqual(store.load().last_lsn, 2)
            # rename 后不应残留 tmp 文件
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_leftover_tmp_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ckpt.json")
            store = CheckpointStore(path)
            store.save(Position(segment="segment-000000.log", offset=10,
                                last_lsn=5))
            # 模拟崩溃留下的半个 tmp 文件
            with open(path + ".tmp", "w") as fh:
                fh.write('{"segment": "seg')
            self.assertEqual(store.load().last_lsn, 5)


if __name__ == "__main__":
    unittest.main()
