import unittest

import strings


class StringsTest(unittest.TestCase):
    def test_shout(self):
        self.assertEqual(strings.shout("hi"), "HI!")

    def test_slugify(self):
        self.assertEqual(strings.slugify("Hello Incremental Checks"), "hello-incremental-checks")


if __name__ == "__main__":
    unittest.main()
