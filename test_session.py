"""会话轮换库自测（仅标准库 unittest）。

覆盖：
1. 新会话
2. 登录提权轮换（旧->新标识对照 + 旧标识失效断言）
3. 携带旧标识的并发请求全部被拒（被拒请求记录）
4. 登出后旧标识重放被拒
5. 边界用例：空标识、未知标识、无效标识提权、重复登出、登出后提权、
   会话固定攻击场景（攻击者持有登录前标识）、标识唯一且不可由客户端指定

运行：python3 -m unittest -v test_session
     python3 test_session.py            # 额外打印审计记录并落盘 audit_records.json
"""

from __future__ import annotations

import json
import threading
import unittest
from datetime import datetime, timezone

from session_lib import SessionError, SessionManager


def _ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.mgr = SessionManager()

    # 1. 新会话 ----------------------------------------------------------
    def test_01_new_session(self):
        sid = self.mgr.create_session()
        self.assertIsInstance(sid, str)
        self.assertGreaterEqual(len(sid), 43)  # 32 字节 urlsafe base64
        self.assertTrue(sid.isascii())

        session = self.mgr.validate(sid)
        self.assertIsNotNone(session)
        self.assertFalse(session.authenticated)   # 新建即匿名
        self.assertIsNone(session.user_id)

        # 服务端生成的两个标识必然不同（标识不可由客户端指定，杜绝固定）
        self.assertNotEqual(sid, self.mgr.create_session())

    # 2. 登录提权轮换 ----------------------------------------------------
    def test_02_login_rotation(self):
        sid = self.mgr.create_session()
        new_sid = self.mgr.elevate(sid, "alice")

        # 更换前后标识对照
        self.assertNotEqual(sid, new_sid)
        self.assertEqual(len(self.mgr.rotation_log), 1)
        rec = self.mgr.rotation_log[0]
        self.assertEqual(rec["old_sid"], sid)
        self.assertEqual(rec["new_sid"], new_sid)
        self.assertEqual(rec["user_id"], "alice")

        # 旧标识立即失效断言
        self.assertIsNone(self.mgr.validate(sid))
        rejected = [r for r in self.mgr.rejection_log if r["sid"] == sid]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "unknown-or-invalidated-sid")

        # 新标识有效且已提权
        session = self.mgr.validate(new_sid)
        self.assertIsNotNone(session)
        self.assertTrue(session.authenticated)
        self.assertEqual(session.user_id, "alice")

    # 3. 携带旧标识的并发请求全部被拒 ------------------------------------
    def test_03_concurrent_old_sid_rejected(self):
        sid = self.mgr.create_session()
        new_sid = self.mgr.elevate(sid, "bob")

        results: list[tuple[str, bool]] = []
        results_lock = threading.Lock()
        start = threading.Barrier(8)

        def worker():
            start.wait()  # 尽量让所有请求同时发起
            ok = self.mgr.validate(sid) is not None
            with results_lock:
                results.append((threading.current_thread().name, ok))

        threads = [threading.Thread(target=worker, name=f"attacker-{i}") for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 断言：8 个并发旧标识请求全部失败
        self.assertEqual(len(results), 8)
        self.assertTrue(all(not ok for _, ok in results))

        # 被拒请求记录：至少 8 条，线程名齐全，原因一致
        rejected = [r for r in self.mgr.rejection_log if r["sid"] == sid]
        self.assertGreaterEqual(len(rejected), 8)
        self.assertEqual({r["thread"] for r in rejected},
                         {f"attacker-{i}" for i in range(8)})
        self.assertTrue(all(r["reason"] == "unknown-or-invalidated-sid" for r in rejected))

        # 提权后的新标识仍然正常
        self.assertEqual(self.mgr.validate(new_sid).user_id, "bob")

    # 4. 登出后重放 ------------------------------------------------------
    def test_04_replay_after_logout(self):
        sid = self.mgr.create_session()
        auth_sid = self.mgr.elevate(sid, "carol")

        self.assertTrue(self.mgr.logout(auth_sid))

        # 登出后标识不再被接受（重放）
        for _ in range(3):
            self.assertIsNone(self.mgr.validate(auth_sid))
        replay = [r for r in self.mgr.rejection_log if r["sid"] == auth_sid]
        self.assertEqual(len(replay), 3)
        self.assertTrue(all(r["reason"] == "unknown-or-invalidated-sid" for r in replay))

        # 登录前的旧标识同样不可用（提权时已轮换作废）
        self.assertIsNone(self.mgr.validate(sid))

    # 5. 边界用例 --------------------------------------------------------
    def test_05_edge_cases(self):
        # 空/缺失标识
        self.assertIsNone(self.mgr.validate(None))
        self.assertIsNone(self.mgr.validate(""))
        # 未知标识
        self.assertIsNone(self.mgr.validate("not-a-real-sid"))
        self.assertTrue(all(r["reason"] != "" for r in self.mgr.rejection_log))

        # 用无效/已失效标识提权必须报错
        with self.assertRaises(SessionError):
            self.mgr.elevate("not-a-real-sid", "mallory")
        with self.assertRaises(SessionError):
            self.mgr.elevate(None, "mallory")
        with self.assertRaises(SessionError):
            self.mgr.elevate("", "mallory")

        # user_id 不能为空
        fresh = self.mgr.create_session()
        with self.assertRaises(SessionError):
            self.mgr.elevate(fresh, "")

        # 重复登出：第一次成功，后续被拒
        auth = self.mgr.elevate(self.mgr.create_session(), "dave")
        self.assertTrue(self.mgr.logout(auth))
        self.assertFalse(self.mgr.logout(auth))

        # 登出后再提权（旧标识）必须失败
        with self.assertRaises(SessionError):
            self.mgr.elevate(auth, "dave-again")

        # 二次提权（如切换账号）继续轮换，中间标识全部失效
        s = self.mgr.create_session()
        s1 = self.mgr.elevate(s, "eve")
        s2 = self.mgr.elevate(s1, "eve-admin")
        self.assertIsNone(self.mgr.validate(s))
        self.assertIsNone(self.mgr.validate(s1))
        self.assertEqual(self.mgr.validate(s2).user_id, "eve-admin")
        self.assertEqual(len(self.mgr.rotation_log), 3)

    # 6. 完整会话固定攻击场景 --------------------------------------------
    def test_06_session_fixation_scenario(self):
        # 攻击者先拿到一个（匿名）会话标识
        attacker_sid = self.mgr.create_session()
        # 诱导受害者用该会话登录；若服务端不轮换，攻击者将直接获得受害者身份。
        victim_sid = self.mgr.elevate(attacker_sid, "victim")

        # 攻击者手里的旧标识已失效，无法冒用受害者身份
        self.assertIsNone(self.mgr.validate(attacker_sid))
        self.assertNotEqual(attacker_sid, victim_sid)
        self.assertEqual(self.mgr.validate(victim_sid).user_id, "victim")


def _print_audit_records(mgr: SessionManager) -> dict:
    print("\n================ 轮换记录（旧标识 -> 新标识）================")
    for r in mgr.rotation_log:
        print(f"[{_ts(r['ts'])}] {r['event']} user={r['user_id']}")
        print(f"  OLD: {r['old_sid']}")
        print(f"  NEW: {r['new_sid']}")

    print("\n================ 拒绝记录 ================")
    for r in mgr.rejection_log:
        print(f"[{_ts(r['ts'])}] thread={r['thread']:<12} "
              f"sid={(r['sid'] or '<empty>')[:24]:<24} reason={r['reason']}")

    print("\n================ 登出记录 ================")
    for r in mgr.logout_log:
        print(f"[{_ts(r['ts'])}] sid={r['sid']} user={r['user_id']}")
    return {
        "rotations": mgr.rotation_log,
        "rejections": mgr.rejection_log,
        "logouts": mgr.logout_log,
    }


if __name__ == "__main__":
    # 先跑完整 unittest 套件
    unittest.main(verbosity=2, exit=False)

    # 再做一次带并发的端到端演示，打印并落盘审计记录
    mgr = SessionManager()
    anon = mgr.create_session()
    auth = mgr.elevate(anon, "demo-user")

    barrier = threading.Barrier(5)
    def replay_old():
        barrier.wait()
        mgr.validate(anon)

    ts = [threading.Thread(target=replay_old, name=f"old-{i}") for i in range(5)]
    for t in ts: t.start()
    for t in ts: t.join()

    mgr.logout(auth)
    mgr.validate(auth)  # 登出后重放

    records = _print_audit_records(mgr)
    with open("audit_records.json", "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print("\n审计记录已写入 audit_records.json")
