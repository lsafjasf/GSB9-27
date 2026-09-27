import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codemetrics.coupling import analyze_tree


def write(root, relpath, content):
    path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class TestCoupling(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        # core: depended on by everyone, depends on nothing -> Ca=3, Ce=0
        write(root, "core.py", "X = 1\n")
        # util: depends on core, depended on by app and cli -> Ca=2, Ce=1
        write(root, "util.py", "import core\n")
        # app: depends on core, util, pkg -> Ca=1 (cli), Ce=3
        write(root, "app.py", "import core\nimport util\nfrom pkg import mod\n")
        # cli: depends on everything -> Ca=0, Ce=3
        write(root, "cli.py", "import core\nimport util\nimport app\n")
        # pkg/mod: leaf inside a package -> Ca=1 (app), Ce=0
        write(root, "pkg/__init__.py", "")
        write(root, "pkg/mod.py", "Y = 2\n")
        self.result = analyze_tree(root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_afferent(self):
        self.assertEqual(self.result["core"].ca, 3)
        self.assertEqual(self.result["util"].ca, 2)
        self.assertEqual(self.result["app"].ca, 1)
        self.assertEqual(self.result["cli"].ca, 0)
        self.assertEqual(self.result["pkg.mod"].ca, 1)

    def test_efferent(self):
        self.assertEqual(self.result["core"].ce, 0)
        self.assertEqual(self.result["util"].ce, 1)
        self.assertEqual(self.result["app"].ce, 3)
        self.assertEqual(self.result["cli"].ce, 3)
        self.assertEqual(self.result["pkg.mod"].ce, 0)

    def test_instability(self):
        self.assertEqual(self.result["core"].instability, 0.0)     # stable
        self.assertEqual(self.result["cli"].instability, 1.0)      # maximally unstable
        self.assertAlmostEqual(self.result["util"].instability, 1 / 3, places=3)

    def test_from_import_submodule(self):
        self.assertIn("pkg.mod", self.result["app"].depends_on)

    def test_depended_by_lists(self):
        self.assertEqual(self.result["core"].depended_by, ["app", "cli", "util"])

    def test_relative_imports(self):
        root = self.tmp.name
        write(root, "pkg/rel.py", "from . import mod\nfrom .. import core\n")
        result = analyze_tree(root)
        self.assertIn("pkg.mod", result["pkg.rel"].depends_on)
        self.assertIn("core", result["pkg.rel"].depends_on)

    def test_external_imports_ignored(self):
        root = self.tmp.name
        write(root, "uses_stdlib.py", "import os\nimport sys\nimport json\n")
        result = analyze_tree(root)
        self.assertEqual(result["uses_stdlib.py".replace(".py", "")].ce
                         if "uses_stdlib" not in result else
                         result["uses_stdlib"].ce, 0)

    def test_syntax_error_module_isolated(self):
        root = self.tmp.name
        write(root, "broken.py", "def broken(:\n")
        result = analyze_tree(root)
        self.assertEqual(result["broken"].ce, 0)
        self.assertEqual(result["broken"].ca, 0)


if __name__ == "__main__":
    unittest.main()
