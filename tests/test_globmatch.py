import unittest

from precheck.globmatch import any_match, match


class GlobMatchTest(unittest.TestCase):
    def test_star_does_not_cross_separator(self):
        self.assertTrue(match("demo/src/a.py", "demo/src/*.py"))
        self.assertFalse(match("demo/src/sub/a.py", "demo/src/*.py"))

    def test_double_star_crosses_separators(self):
        for pattern in ("demo/src/**.py", "demo/src/**/*.py"):
            self.assertTrue(match("demo/src/a.py", pattern), pattern)
            self.assertTrue(match("demo/src/sub/a.py", pattern), pattern)

    def test_double_star_dir_prefix_zero_or_more_segments(self):
        pattern = "demo/**/a.py"
        self.assertTrue(match("demo/a.py", pattern))
        self.assertTrue(match("demo/x/a.py", pattern))
        self.assertTrue(match("demo/x/y/a.py", pattern))

    def test_prefix_double_star(self):
        self.assertTrue(match("README.md", "**.md"))
        self.assertTrue(match("demo/docs/guide.md", "**.md"))
        self.assertFalse(match("demo/src/a.py", "**.md"))

    def test_any_match(self):
        self.assertTrue(any_match("demo/src/a.py", ["demo/tests/**.py", "demo/src/**.py"]))
        self.assertFalse(any_match("README.md", ["demo/src/**.py"]))

    def test_question_mark(self):
        self.assertTrue(match("a1.py", "a?.py"))
        self.assertFalse(match("a12.py", "a?.py"))


if __name__ == "__main__":
    unittest.main()
