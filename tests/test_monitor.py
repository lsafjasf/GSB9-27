"""修复版 FIM 的回归测试。

运行：python3 -m unittest discover -s tests -v
"""

import hashlib
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fim.audit import AuditLog
from fim.authjournal import AuthorizationJournal
from fim.legacy import LegacyMonitor
from fim.monitor import IntegrityMonitor
from fim.rules import Rule, RuleSet, DEFAULT_RULES


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class MonitorTestBase(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="fim-test-")
        self.root = os.path.join(self.workdir, "root")
        os.makedirs(self.root)
        self.audit = AuditLog(None)
        self.journal = AuthorizationJournal(os.path.join(self.workdir, "journal.jsonl"))
        self.mon = IntegrityMonitor(self.root, audit=self.audit, auth=self.journal)

    def tearDown(self):
        shutil.rmtree(self.workdir, ignore_errors=True)

    def write(self, rel, data):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)

    def read(self, rel):
        with open(os.path.join(self.root, rel), "rb") as fh:
            return fh.read()

    def full(self, rel):
        return os.path.join(self.root, rel)

    def audit_events(self, event):
        return [r for r in self.audit.records if r["event"] == event]


class TestDetection(MonitorTestBase):
    """检出能力：真实篡改必须告警。"""

    def test_stealth_tamper_same_size_and_mtime_detected(self):
        """核心用例：等长替换内容并恢复 mtime，元数据完全不变，仍须检出。"""
        data = b"\x7fELF important-binary " + b"A" * 64
        self.write("usr/bin/tool", data)
        self.mon.baseline()

        st = os.stat(self.full("usr/bin/tool"))
        forged = b"\x90" * 8 + data[8:]  # 等长
        self.assertEqual(len(forged), len(data))
        self.write("usr/bin/tool", forged)
        os.utime(self.full("usr/bin/tool"), ns=(st.st_atime_ns, st.st_mtime_ns))

        alerts = self.mon.scan()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].kind, "CONTENT_CHANGED")
        self.assertEqual(alerts[0].path, "usr/bin/tool")
        self.assertEqual(alerts[0].detail["old_size"], alerts[0].detail["new_size"])

    def test_legacy_misses_stealth_tamper(self):
        """对照：旧实现漏掉同一隐蔽篡改（复现漏报）。"""
        data = b"\x7fELF important-binary " + b"A" * 64
        self.write("usr/bin/tool", data)
        legacy = LegacyMonitor(self.root)
        legacy.baseline()

        st = os.stat(self.full("usr/bin/tool"))
        self.write("usr/bin/tool", b"\x90" * 8 + data[8:])
        os.utime(self.full("usr/bin/tool"), ns=(st.st_atime_ns, st.st_mtime_ns))

        self.assertEqual(legacy.scan(), [])

    def test_atomic_replace_tamper_detected(self):
        """原子替换（写临时文件 + rename）篡改必须检出，并标注 method。"""
        self.write("etc/app.conf", b"mode=safe\n")
        self.mon.baseline()

        tmp = self.full("etc/.app.conf.tmp")
        with open(tmp, "wb") as fh:
            fh.write(b"mode=EVIL\n")
        os.replace(tmp, self.full("etc/app.conf"))

        alerts = self.mon.scan()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].kind, "CONTENT_CHANGED")
        self.assertEqual(alerts[0].detail["method"], "atomic_rename")

    def test_unauthorized_new_file_detected(self):
        self.mon.baseline()
        self.write("usr/bin/evil", b"backdoor")
        alerts = self.mon.scan()
        self.assertEqual([(a.path, a.kind) for a in alerts],
                         [("usr/bin/evil", "CREATED")])

    def test_delete_monitored_file_detected(self):
        self.write("etc/keys/main.key", b"secret")
        self.mon.baseline()
        os.remove(self.full("etc/keys/main.key"))
        alerts = self.mon.scan()
        self.assertEqual([(a.path, a.kind) for a in alerts],
                         [("etc/keys/main.key", "DELETED")])

    def test_baseline_is_silent(self):
        self.write("etc/app.conf", b"x=1\n")
        self.write("var/log/app.log", b"log\n")
        self.assertEqual(self.mon.scan(), [])
        self.assertEqual(self.mon.scan(), [])


class TestNoiseSuppression(MonitorTestBase):
    """噪声抑制：良性变化不告警，但全部可审计。"""

    def test_permission_change_same_content_no_alert(self):
        """内容相同、仅权限变化：不告警，写审计。"""
        self.write("usr/bin/tool", b"binary")
        self.mon.baseline()
        os.chmod(self.full("usr/bin/tool"), 0o750)

        self.assertEqual(self.mon.scan(), [])
        recs = self.audit_events("permission_changed")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["path"], "usr/bin/tool")
        self.assertEqual(recs[0]["new_mode"], "0750")

    def test_permission_change_alert_when_enabled(self):
        mon = IntegrityMonitor(self.root, audit=self.audit, auth=self.journal,
                               alert_on_permission_change=True)
        self.write("usr/bin/tool", b"binary")
        mon.baseline()
        os.chmod(self.full("usr/bin/tool"), 0o700)
        alerts = mon.scan()
        self.assertEqual([a.kind for a in alerts], ["PERMISSION_CHANGED"])

    def test_atomic_rewrite_same_content_no_alert(self):
        """原子替换后内容逐字节相同：不告警，写审计。"""
        self.write("etc/app.conf", b"mode=safe\n")
        self.mon.baseline()
        tmp = self.full("etc/.app.conf.tmp")
        with open(tmp, "wb") as fh:
            fh.write(b"mode=safe\n")
        os.replace(tmp, self.full("etc/app.conf"))

        self.assertEqual(self.mon.scan(), [])
        self.assertEqual(len(self.audit_events("atomic_rewrite_same_content")), 1)

    def test_delete_then_recreate_identical_no_alert(self):
        """删除后按原内容重建：不告警。"""
        data = b"1.2.3\n"
        self.write("opt/app/VERSION", data)
        self.mon.baseline()
        old_ino = os.stat(self.full("opt/app/VERSION")).st_ino
        os.remove(self.full("opt/app/VERSION"))
        self.write("opt/app/VERSION", data)

        self.assertEqual(self.mon.scan(), [])
        # inode 复用时无结构变化可记录；inode 变化时必须写审计
        new_ino = os.stat(self.full("opt/app/VERSION")).st_ino
        if new_ino != old_ino:
            self.assertEqual(len(self.audit_events("atomic_rewrite_same_content")), 1)

    def test_touch_only_no_alert(self):
        self.write("etc/app.conf", b"x=1\n")
        self.mon.baseline()
        os.utime(self.full("etc/app.conf"), None)
        self.assertEqual(self.mon.scan(), [])

    def test_temp_files_excluded_and_audited(self):
        """临时路径的创建/删除：不告警，规则命中写审计。"""
        self.mon.baseline()
        self.write("tmp/build-1.o", b"obj")
        self.write("cache/cc-1", b"cache")
        alerts = self.mon.scan()
        self.assertEqual(alerts, [])
        hits = {r["rule_hit"] for r in self.audit_events("excluded_created")}
        self.assertEqual(hits, {"R001-TMPDIR", "R002-CACHE"})

        os.remove(self.full("tmp/build-1.o"))
        self.assertEqual(self.mon.scan(), [])
        self.assertEqual(len(self.audit_events("excluded_deleted")), 1)

    def test_log_rotation_excluded_and_audited(self):
        """日志轮转（rename + 新建）：不告警，规则命中写审计。"""
        self.write("var/log/app.log", b"line1\n")
        self.mon.baseline()
        os.rename(self.full("var/log/app.log"), self.full("var/log/app.log.1"))
        self.write("var/log/app.log", b"")

        self.assertEqual(self.mon.scan(), [])
        created = {r["path"]: r["rule_hit"] for r in self.audit_events("excluded_created")}
        replaced = {r["path"]: r["rule_hit"] for r in self.audit_events("excluded_replaced")}
        self.assertEqual(created.get("var/log/app.log.1"), "R004-LOG-ROTATED")
        self.assertEqual(replaced.get("var/log/app.log"), "R005-LOG-LIVE")

    def test_authorized_update_suppressed_but_audited(self):
        """预登记的合法更新：不告警，写审计。"""
        self.write("usr/bin/tool", b"v1")
        self.mon.baseline()
        new = b"v2"
        self.journal.authorize("usr/bin/tool", sha256(new), actor="pkg-mgr")
        self.write("usr/bin/tool", new)

        self.assertEqual(self.mon.scan(), [])
        recs = self.audit_events("authorized_update")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["actor"], "pkg-mgr")

    def test_authorization_is_single_use(self):
        """授权一次性：回滚到历史授权版本必须告警（防 rollback 绕过）。"""
        self.write("usr/bin/tool", b"v1")
        self.mon.baseline()
        self.journal.authorize("usr/bin/tool", sha256(b"v2"), actor="pkg-mgr")
        self.write("usr/bin/tool", b"v2")
        self.assertEqual(self.mon.scan(), [])  # 第一次：合法更新，授权被消费

        self.write("usr/bin/tool", b"v1")  # 攻击者回滚
        alerts = self.mon.scan()
        self.assertEqual([a.kind for a in alerts], ["CONTENT_CHANGED"])

    def test_unauthorized_content_change_alerts(self):
        """未登记的内容变化：告警。"""
        self.write("usr/bin/tool", b"v1")
        self.mon.baseline()
        self.write("usr/bin/tool", b"v2")
        alerts = self.mon.scan()
        self.assertEqual([a.kind for a in alerts], ["CONTENT_CHANGED"])


class TestRules(unittest.TestCase):
    def test_glob_double_star(self):
        rs = RuleSet([Rule("T", "**/tmp/**", "tmp")])
        self.assertIsNotNone(rs.match("a/tmp/x"))
        self.assertIsNotNone(rs.match("tmp/x"))
        self.assertIsNotNone(rs.match("a/b/tmp/c/d"))
        self.assertIsNone(rs.match("a/tmpx/y"))

    def test_glob_char_class(self):
        rs = RuleSet([Rule("T", "**/log/*.log.[0-9]*", "rotated")])
        self.assertIsNotNone(rs.match("var/log/app.log.1"))
        self.assertIsNotNone(rs.match("var/log/app.log.12.gz"))
        self.assertIsNone(rs.match("var/log/app.log"))
        self.assertIsNone(rs.match("var/log/app.log.txt"))

    def test_first_match_wins_and_duplicate_id_rejected(self):
        rs = RuleSet([Rule("A", "**/*.log", "1"), Rule("B", "**/var/**", "2")])
        self.assertEqual(rs.match("var/x.log").rule_id, "A")
        with self.assertRaises(ValueError):
            rs.add(Rule("A", "x", "dup"))

    def test_default_rules_cover_declared_categories(self):
        rs = RuleSet(DEFAULT_RULES)
        cases = {
            "srv/app/tmp/pid": "R001-TMPDIR",
            "srv/app/cache/page": "R002-CACHE",
            "srv/app/build/out.o": "R003-BUILD",
            "var/log/app.log.3.gz": "R004-LOG-ROTATED",
            "var/log/app.log": "R005-LOG-LIVE",
            "src/__pycache__/m.cpython-312.pyc": "R009-PY-PYC",
        }
        for path, rule_id in cases.items():
            self.assertEqual(rs.match(path).rule_id, rule_id, path)
        self.assertIsNone(rs.match("etc/app.conf"))


if __name__ == "__main__":
    unittest.main()
