"""fieldcipher 自测：python3 -m unittest -v"""

import os
import sqlite3
import struct
import threading
import unittest

from fieldcipher import (
    FieldCipher,
    FieldTooLong,
    IntegrityError,
    KeyRing,
    UnknownKeyVersion,
    make_aad,
    rotate_table,
)

KEY_V1 = bytes(range(32))
KEY_V2 = bytes(range(32, 64))
CTX = b"users.email"
TF = "users.email"


def make_cipher(keyring=None, **kw):
    return FieldCipher(keyring or KeyRing({1: KEY_V1}, active=1), CTX, **kw)


class RoundTripTest(unittest.TestCase):
    def test_roundtrip_and_randomized_ciphertext(self):
        c = make_cipher()
        ct1, tag1 = c.encrypt("Alice@Example.com", make_aad(TF, 1))
        ct2, tag2 = c.encrypt("Alice@Example.com", make_aad(TF, 2))
        self.assertNotEqual(ct1, ct2)          # 密文随机化（nonce）
        self.assertEqual(tag1, tag2)           # 标签确定性 -> 可等值查询
        self.assertEqual(c.decrypt(ct1, make_aad(TF, 1)), "Alice@Example.com")

    def test_tag_normalization(self):
        c = make_cipher()
        # NFKC + casefold：全角/大小写差异不影响等值标签，但密文保留原文
        self.assertEqual(c.tag_for("ＡＬＩＣＥ@x.com"), c.tag_for("alice@x.com"))
        ct, _ = c.encrypt("ＡＬＩＣＥ@x.com", make_aad(TF, 1))
        self.assertEqual(c.decrypt(ct, make_aad(TF, 1)), "ＡＬＩＣＥ@x.com")

    def test_tag_domain_separation(self):
        c1, c2 = make_cipher(), FieldCipher(KeyRing({1: KEY_V1}, 1), b"users.phone")
        self.assertNotEqual(c1.tag_for("same"), c2.tag_for("same"))

    def test_null_vs_empty(self):
        c = make_cipher()
        ct, tag = c.encrypt(None, make_aad(TF, 1))
        self.assertIsNone(ct)
        self.assertIsNone(tag)                 # NULL 不落标签，查询用 IS NULL
        self.assertIsNone(c.decrypt(None, make_aad(TF, 1)))
        ct_empty, tag_empty = c.encrypt("", make_aad(TF, 1))
        self.assertIsNotNone(ct_empty)         # 空串与 NULL 严格区分
        self.assertEqual(c.decrypt(ct_empty, make_aad(TF, 1)), "")
        self.assertNotEqual(tag_empty, c.tag_for("x"))

    def test_overlong_field_rejected(self):
        c = make_cipher(max_plaintext=64)
        with self.assertRaises(FieldTooLong):
            c.encrypt("x" * 65, make_aad(TF, 1))
        ct, _ = c.encrypt("x" * 64, make_aad(TF, 1))   # 边界值可用
        self.assertEqual(c.decrypt(ct, make_aad(TF, 1)), "x" * 64)

    def test_max_plaintext_boundary_default(self):
        c = make_cipher()
        big = "界" * 10000  # 30000 字节 UTF-8，低于 64KiB 上限
        ct, _ = c.encrypt(big, make_aad(TF, 1))
        self.assertEqual(c.decrypt(ct, make_aad(TF, 1)), big)

    def test_length_padding(self):
        c = make_cipher(pad_to=64)
        lengths = {len(c.encrypt("a" * n, make_aad(TF, 1))[0]) for n in (1, 20, 40)}
        self.assertEqual(len(lengths), 1)      # 同桶内密文长度一致
        for n in (1, 20, 40):
            ct, _ = c.encrypt("a" * n, make_aad(TF, 1))
            self.assertEqual(c.decrypt(ct, make_aad(TF, 1)), "a" * n)


class IntegrityTest(unittest.TestCase):
    def test_tamper_detected(self):
        c = make_cipher()
        ct, _ = c.encrypt("secret", make_aad(TF, 1))
        for pos in (0, 10, len(ct) - 20, len(ct) - 1):
            bad = bytearray(ct)
            bad[pos] ^= 1
            with self.assertRaises(IntegrityError):
                c.decrypt(bytes(bad), make_aad(TF, 1))

    def test_cross_record_copy_detected(self):
        """核心用例：把记录 1 的密文整列复制到记录 2，解密必须失败。"""
        c = make_cipher()
        ct, tag = c.encrypt("victim@example.com", make_aad(TF, 1))
        # 攻击者（有库读写权限）把密文复制到记录 2
        with self.assertRaises(IntegrityError):
            c.decrypt(ct, make_aad(TF, 2))
        # 跨字段复制同样失败
        with self.assertRaises(IntegrityError):
            c.decrypt(ct, make_aad("users.backup_email", 1))
        # 原记录正常
        self.assertEqual(c.decrypt(ct, make_aad(TF, 1)), "victim@example.com")

    def test_truncation_and_garbage(self):
        c = make_cipher()
        ct, _ = c.encrypt("hello", make_aad(TF, 1))
        for bad in (b"", b"FC1", ct[:20], ct[:-1], os.urandom(40)):
            with self.assertRaises(IntegrityError):
                c.decrypt(bad, make_aad(TF, 1))


class KeyRotationTest(unittest.TestCase):
    def test_rotation_and_old_key_retirement(self):
        ring1 = KeyRing({1: KEY_V1}, active=1)
        c1 = make_cipher(ring1)
        ct_old, tag_old = c1.encrypt("rotate@me.com", make_aad(TF, 7))

        ring2 = KeyRing({1: KEY_V1, 2: KEY_V2}, active=2)
        c2 = make_cipher(ring2)
        ct_new, tag_new = c2.rotate_value(ct_old, make_aad(TF, 7))
        self.assertEqual(struct.unpack(">I", ct_new[3:7])[0], 2)   # 信封版本号已切换
        self.assertNotEqual(tag_old, tag_new)                        # 标签随版本重建
        self.assertEqual(c2.decrypt(ct_new, make_aad(TF, 7)), "rotate@me.com")
        # 过渡期内旧密文仍可读（双版本并存）
        self.assertEqual(c2.decrypt(ct_old, make_aad(TF, 7)), "rotate@me.com")
        # 轮换完成后退役 v1：旧密文不可再解，报 UnknownKeyVersion
        c3 = make_cipher(KeyRing({2: KEY_V2}, active=2))
        with self.assertRaises(UnknownKeyVersion):
            c3.decrypt(ct_old, make_aad(TF, 7))
        self.assertEqual(c3.decrypt(ct_new, make_aad(TF, 7)), "rotate@me.com")

    def test_rotation_idempotent(self):
        c1 = make_cipher()
        ct, _ = c1.encrypt("x", make_aad(TF, 1))
        c2 = make_cipher(KeyRing({1: KEY_V1, 2: KEY_V2}, active=2))
        once, _ = c2.rotate_value(ct, make_aad(TF, 1))
        twice, _ = c2.rotate_value(once, make_aad(TF, 1))
        self.assertEqual(c2.decrypt(twice, make_aad(TF, 1)), "x")


class SqliteScenarioTest(unittest.TestCase):
    """端到端：SQLite 表 + 等值查询 + 断点续跑轮换 + 并发写入。"""

    def setUp(self):
        self.db = tempfile_db()
        self.db.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, ct BLOB, tag BLOB)"
        )
        self.cipher = make_cipher()

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    @property
    def db_path(self):
        return self._path

    def insert(self, cipher, record_id, value):
        ct, tag = cipher.encrypt(value, make_aad(TF, record_id))
        self.db.execute(
            "INSERT INTO users (id, ct, tag) VALUES (?, ?, ?)",
            (record_id, ct, tag),
        )
        self.db.commit()

    def test_equality_query_via_tag(self):
        for i, mail in enumerate(["a@x.com", "b@x.com", "a@x.com", None], start=1):
            self.insert(self.cipher, i, mail)
        rows = self.db.execute(
            "SELECT id, ct FROM users WHERE tag = ?",
            (self.cipher.tag_for("A@x.COM"),),   # 归一化后命中
        ).fetchall()
        self.assertEqual(sorted(r[0] for r in rows), [1, 3])
        for rid, ct in rows:
            self.assertEqual(self.cipher.decrypt(ct, make_aad(TF, rid)), "a@x.com")
        nulls = self.db.execute("SELECT id FROM users WHERE tag IS NULL").fetchall()
        self.assertEqual([r[0] for r in nulls], [4])

    def test_interrupted_rotation_resumes(self):
        for i in range(1, 26):
            self.insert(self.cipher, i, f"user{i}@x.com")
        rotated = make_cipher(KeyRing({1: KEY_V1, 2: KEY_V2}, active=2))

        calls = {"n": 0}
        def crash_after_two_batches():
            calls["n"] += 1
            return calls["n"] > 2   # 第 3 批前模拟进程崩溃

        with self.assertRaises(InterruptedError):
            rotate_table(self.db, rotated, table="users", id_column="id",
                         ct_column="ct", tag_column="tag", table_field=TF,
                         batch_size=5, should_abort=crash_after_two_batches)
        # 崩溃后：部分行 v2、部分行 v1，但全部可读（双版本并存）
        versions = {struct.unpack(">I", r[0][3:7])[0]
                    for r in self.db.execute("SELECT ct FROM users")}
        self.assertEqual(versions, {1, 2})
        # 断点续跑：幂等完成剩余部分
        n = rotate_table(self.db, rotated, table="users", id_column="id",
                         ct_column="ct", tag_column="tag", table_field=TF,
                         batch_size=5)
        self.assertGreater(n, 0)
        versions = {struct.unpack(">I", r[0][3:7])[0]
                    for r in self.db.execute("SELECT ct FROM users")}
        self.assertEqual(versions, {2})
        for rid, (ct,) in enumerate(
            self.db.execute("SELECT ct FROM users ORDER BY id"), start=1
        ):
            self.assertEqual(rotated.decrypt(ct, make_aad(TF, rid)), f"user{rid}@x.com")

    def test_concurrent_writes(self):
        """多线程并发写入：每条记录独立 nonce + 原子事务，互不干扰且全部可校验。"""
        path = self.db_path
        errors = []

        def worker(tid):
            try:
                conn = sqlite3.connect(path, timeout=30)
                for i in range(20):
                    rid = tid * 1000 + i
                    value = f"user{rid}@x.com"
                    ct, tag = self.cipher.encrypt(value, make_aad(TF, rid))
                    with conn:
                        conn.execute(
                            "INSERT INTO users (id, ct, tag) VALUES (?, ?, ?)",
                            (rid, ct, tag),
                        )
                conn.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        rows = self.db.execute("SELECT id, ct, tag FROM users").fetchall()
        self.assertEqual(len(rows), 160)
        for rid, ct, tag in rows:
            self.assertEqual(
                self.cipher.decrypt(ct, make_aad(TF, rid)), f"user{rid}@x.com"
            )
            self.assertEqual(tag, self.cipher.tag_for(f"user{rid}@x.com"))

    def test_concurrent_same_value_same_tag(self):
        """并发写相同明文：确定性标签天然幂等，不产生写冲突。"""
        t1 = self.cipher.tag_for("shared@x.com")
        t2 = make_cipher().tag_for("shared@x.com")
        self.assertEqual(t1, t2)


def tempfile_db():
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    SqliteScenarioTest._path = path  # 供 tearDown 删除
    return conn


if __name__ == "__main__":
    unittest.main()
