"""Shared test helpers: materialize a fresh copy of demo_repo per test."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from inccheck import engine  # noqa: E402
from inccheck.model import Change  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEMO = os.path.join(REPO_ROOT, "demo_repo")


class RepoCase(unittest.TestCase):
    """Gives each test a pristine copy of demo_repo in a temp dir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="inccheck-test-")
        self.root = os.path.join(self.tmp, "repo")
        shutil.copytree(DEMO, self.root,
                        ignore=shutil.ignore_patterns(".inccheck"))
        self.repo = engine.Repo(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- file helpers -------------------------------------------------
    def path(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def write(self, rel, text):
        os.makedirs(os.path.dirname(self.path(rel)), exist_ok=True)
        with open(self.path(rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def append(self, rel, text):
        with open(self.path(rel), "a", encoding="utf-8") as fh:
            fh.write(text)

    def delete(self, rel):
        os.unlink(self.path(rel))

    def read(self, rel):
        with open(self.path(rel), encoding="utf-8") as fh:
            return fh.read()

    # -- engine helpers ------------------------------------------------
    def baseline(self):
        """Establish the baseline cache via a full run."""
        return engine.run_full(self.repo)

    def inc_report(self, changes):
        result = engine.run_incremental(self.repo, changes)
        return engine.render_report(result.findings, self.root), result

    def full_report(self):
        findings = engine.run_full(self.repo)
        return engine.render_report(findings, self.root), findings

    def assert_equivalent(self, changes):
        """Incremental merged output must equal a fresh full run, and every
        post-change failure must be covered by the incremental selection."""
        inc_text, result = self.inc_report(changes)
        full_text, _ = self.full_report()
        self.assertEqual(inc_text, full_text,
                         "incremental and full reports diverge")
        self.assertEqual(result.metrics["recall"], 1.0)
        return result


def C(status, path):
    return Change(status, path)
