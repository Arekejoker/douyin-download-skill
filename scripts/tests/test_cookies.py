import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import get_douyin_cookies as GC  # noqa: E402


class NetscapeTest(unittest.TestCase):
    def test_lines(self):
        cookies = [
            {"domain": ".douyin.com", "path": "/", "secure": True,
             "expirationDate": 4102444800, "name": "ttwid", "value": "abc"},
            {"domain": "www.douyin.com", "path": "/x", "secure": False,
             "name": "a", "value": "b"},
        ]
        lines = GC.netscape_lines(cookies)
        self.assertEqual(lines[0], "# Netscape HTTP Cookie File")
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[1].startswith(".douyin.com\tTRUE"))
        self.assertIn("\tttwid\tabc", lines[1])
        self.assertTrue(lines[2].startswith("www.douyin.com\tFALSE"))

    def test_missing_expiry_gets_default(self):
        lines = GC.netscape_lines([{"name": "x", "value": "y"}])
        self.assertEqual(len(lines[1].split("\t")), 7)

    def test_write_perms(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.txt")
            GC.write_netscape([{"name": "x", "value": "y"}], p)
            self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")
            with open(p) as f:
                self.assertIn("# Netscape HTTP Cookie File", f.read())


if __name__ == "__main__":
    unittest.main()
