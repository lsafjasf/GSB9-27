"""Regression tests for the fixed FIM monitor.

Run: python3 -m unittest discover -s fim/tests -v
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest

from fim.baseline import NaiveMonitor
from fim.fmonitor import (
    ADMITTED_UPDATE,
    FixedMonitor,
    IDENTICAL_REPLACE,
    PERMS_CHANGED,
    RULE_HIT,
    TAMPER_CONTENT,
    TAMPER_DELETED,
    TAMPER_PERMS,
    UNEXPECTED_CREATED,
)
from fim.rules import RuleSet


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FimCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fim-test-")
        self.root = os.path.join(self.tmp, "watch")
        os.makedirs(self.root)
        self.state = os.path.join(self.tmp, "state", "state.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, content, mode=None, mtime_ns=None):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(content if isinstance(content, bytes) else content.encode())
        if mode is not None:
            os.chmod(path, mode)
        if mtime_ns is not None:
            os.utime(path, ns=(mtime_ns, mtime_ns))
        return path

    def monitor(self):
        return FixedMonitor(self.root, self.state, rules=RuleSet.load())

    def audit_records(self, mon):
        with open(mon.audit_path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    # ------------------------------------------------- detection invariants
    def test_same_size_forged_mtime_tamper_is_detected(self):
        """Critical regression: replaced content, same size, mtime restored."""
        path = self.write("etc/hosts", "127.0.0.1 localhost\n")
        mon = self.monitor()
        mon.baseline()

        with open(path) as _fh:
            original = _fh.read()
        mtime = os.stat(path).st_mtime_ns
        evil = original.replace("localhost", "127.0.0.2")
        self.assertNotEqual(evil, original)
        self.assertEqual(len(evil), len(original))  # equal length
        with open(path, "w") as fh:
            fh.write(evil)
        os.utime(path, ns=(mtime, mtime))  # attacker forges timestamp

        res = mon.scan()
        kinds = [(e.kind, e.path) for e in res.alerts]
        self.assertIn((TAMPER_CONTENT, "etc/hosts"), kinds)
        alert = next(e for e in res.alerts if e.path == "etc/hosts")
        self.assertTrue(alert.detail["stat_forged"])
        self.assertTrue(alert.detail["size_same"])
        self.assertTrue(alert.detail["mtime_same"])

    def test_naive_baseline_misses_forged_tamper(self):
        """Documents the pre-fix blind spot that the fix must close."""
        path = self.write("etc/hosts", "127.0.0.1 localhost\n")
        naive = NaiveMonitor(self.root, os.path.join(self.tmp, "n.json"))
        naive.baseline()
        with open(path) as _fh:
            original = _fh.read()
        mtime = os.stat(path).st_mtime_ns
        with open(path, "w") as fh:
            fh.write(original.replace("localhost", "127.0.0.2"))
        os.utime(path, ns=(mtime, mtime))
        self.assertEqual(naive.scan().alerts, [])

    def test_atomic_replace_different_content_detected(self):
        path = self.write("usr/bin/healthcheck", "#!/bin/sh\nexit 0\n", mode=0o755)
        mon = self.monitor()
        mon.baseline()
        mtime = os.stat(path).st_mtime_ns
        evil = "#!/bin/sh\nexit 1\n"
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(evil)
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)
        os.utime(path, ns=(mtime, mtime))
        self.assertNotEqual(os.stat(path).st_ino, mon.state["usr/bin/healthcheck"]["inode"])
        res = mon.scan()
        self.assertEqual([(e.kind, e.path) for e in res.alerts],
                         [(TAMPER_CONTENT, "usr/bin/healthcheck")])

    def test_atomic_replace_identical_content_is_audit_only(self):
        path = self.write("etc/config.ini", "key=value\n")
        mon = self.monitor()
        mon.baseline()
        old_inode = os.stat(path).st_ino
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write("key=value\n")
        os.replace(tmp, path)
        self.assertNotEqual(os.stat(path).st_ino, old_inode)
        res = mon.scan()
        self.assertEqual(res.alerts, [])
        self.assertEqual([e.kind for e in res.audit if e.path == "etc/config.ini"],
                         [IDENTICAL_REPLACE])

    def test_delete_and_recreate_tamper_detected(self):
        path = self.write("etc/cron.d/job", "0 * * * * /bin/true\n")
        mon = self.monitor()
        mon.baseline()
        mtime = os.stat(path).st_mtime_ns
        os.remove(path)
        self.write("etc/cron.d/job", "0 * * * * /bin/evl\n", mtime_ns=mtime)
        res = mon.scan()
        self.assertEqual([(e.kind, e.path) for e in res.alerts],
                         [(TAMPER_CONTENT, "etc/cron.d/job")])

    def test_unexpected_deletion_alerts(self):
        self.write("etc/important", "data\n")
        mon = self.monitor()
        mon.baseline()
        os.remove(os.path.join(self.root, "etc/important"))
        res = mon.scan()
        self.assertEqual([(e.kind, e.path) for e in res.alerts],
                         [(TAMPER_DELETED, "etc/important")])

    def test_unexpected_creation_alerts(self):
        mon = self.monitor()
        mon.baseline()
        self.write("etc/newfile", "x\n")
        res = mon.scan()
        self.assertEqual([(e.kind, e.path) for e in res.alerts],
                         [(UNEXPECTED_CREATED, "etc/newfile")])

    # ------------------------------------------------------------- noise
    def test_excluded_paths_never_alert_and_are_audited(self):
        self.write("tmp/scratch.txt", "a\n")
        self.write("etc/.hosts.swp", "b\n")
        self.write("notes.txt.bak", "c\n")
        self.write("src/__pycache__/m.cpython-312.pyc", "d\n")
        self.write("build/output.o", "e\n")
        self.write("src/app.pyo", "f\n")
        self.write("var/app.log", "g\n")
        self.write("var/app.log.1.gz", "h\n")
        self.write("var/app.err.2026-09-28.gz", "i\n")
        self.write("etc/real.conf", "keep\n")
        mon = self.monitor()
        mon.baseline()
        self.assertEqual(set(mon.state), {"etc/real.conf"})

        # churn the excluded files, rotate the log: still zero alerts
        self.write("tmp/scratch.txt", "aa\n")
        os.remove(os.path.join(self.root, "var/app.log"))
        self.write("var/app.log", "new boot\n")
        self.write("var/app.log.2.gz", "rotated\n")
        res = mon.scan()
        self.assertEqual(res.alerts, [])
        hit_paths = {e.path for e in res.audit if e.kind == RULE_HIT}
        self.assertIn("tmp/", hit_paths)
        self.assertIn("var/app.log", hit_paths)
        self.assertIn("var/app.log.2.gz", hit_paths)
        # rule hits must be persisted for auditability
        records = self.audit_records(mon)
        self.assertTrue(any(r["kind"] == "rule_hit" for r in records))
        for rec in records:
            if rec["kind"] == "rule_hit":
                self.assertIn("rule", rec["detail"])

    def test_permission_only_change_is_audit_not_alert(self):
        self.write("bin/tool.sh", "#!/bin/sh\necho ok\n", mode=0o755)
        mon = self.monitor()
        mon.baseline()
        os.chmod(os.path.join(self.root, "bin/tool.sh"), 0o750)
        res = mon.scan()
        self.assertEqual(res.alerts, [])
        perms = [e for e in res.audit if e.path == "bin/tool.sh"]
        self.assertEqual([e.kind for e in perms], [PERMS_CHANGED])

    def test_dangerous_permission_change_escalates(self):
        self.write("bin/tool.sh", "#!/bin/sh\necho ok\n", mode=0o755)
        mon = self.monitor()
        mon.baseline()
        os.chmod(os.path.join(self.root, "bin/tool.sh"), 0o4755)  # setuid
        res = mon.scan()
        self.assertEqual([(e.kind, e.path) for e in res.alerts],
                         [(TAMPER_PERMS, "bin/tool.sh")])
        # world-writable also escalates
        os.chmod(os.path.join(self.root, "bin/tool.sh"), 0o777)
        res = mon.scan()
        self.assertTrue(any(e.kind == TAMPER_PERMS for e in res.alerts))

    # ------------------------------------------------- admission workflow
    def test_admitted_update_does_not_alert(self):
        path = self.write("etc/app.conf", "version=1\n")
        mon = self.monitor()
        mon.baseline()
        new_content = "version=2\nfeature=true\n"
        mon.admit("etc/app.conf", operator="deploy", reason="CR-1001",
                  expected_digest=sha(new_content.encode()))
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(new_content)
        os.replace(tmp, path)
        res = mon.scan()
        self.assertEqual(res.alerts, [])
        self.assertTrue(any(e.kind == ADMITTED_UPDATE and e.path == "etc/app.conf"
                            for e in res.audit))
        # admission is single-use: a further unannounced change alerts
        with open(path, "w") as fh:
            fh.write("version=999-evil\n")
        res = mon.scan()
        self.assertTrue(any(e.kind == TAMPER_CONTENT for e in res.alerts))

    def test_admitted_delete_recreate_is_audit_only(self):
        path = self.write("var/state.cache", "v=old\n")
        mon = self.monitor()
        mon.baseline()
        mon.admit("var/state.cache", operator="ops", reason="cache rebuild",
                  expected_digest=sha(b"v=new\n"))
        os.remove(path)
        self.write("var/state.cache", "v=new\n")
        res = mon.scan()
        self.assertEqual(res.alerts, [])
        self.assertTrue(any(e.kind == ADMITTED_UPDATE for e in res.audit))

    def test_admission_wrong_digest_is_warned_but_audited(self):
        self.write("etc/app.conf", "version=1\n")
        mon = self.monitor()
        mon.baseline()
        mon.admit("etc/app.conf", operator="deploy", reason="CR-x",
                  expected_digest=sha(b"declared\n"))
        self.write("etc/app.conf", "actually-deployed\n")
        res = mon.scan()
        self.assertEqual(res.alerts, [])
        ev = next(e for e in res.audit if e.kind == ADMITTED_UPDATE)
        self.assertIn("admission_warning", ev.detail["admission"])

    def test_state_persists_across_restarts(self):
        self.write("etc/hosts", "data\n")
        mon = self.monitor()
        mon.baseline()
        mon2 = self.monitor()  # new process, same state file
        self.write("etc/hosts", "DATX\n")
        res = mon2.scan()
        self.assertTrue(any(e.kind == TAMPER_CONTENT for e in res.alerts))

    def test_rules_loaded_from_manifest(self):
        rs = RuleSet.load()
        self.assertEqual({r.category for r in rs.rules}, {"temp", "generated", "logs"})
        self.assertIsNone(rs.match("etc/app.conf"))
        self.assertEqual(rs.match("tmp/x").category, "temp")
        self.assertEqual(rs.match("src/__pycache__/m.pyc").category, "generated")
        self.assertEqual(rs.match("var/app.log.1.gz").category, "logs")


if __name__ == "__main__":
    unittest.main()
