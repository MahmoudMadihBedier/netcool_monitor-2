"""Filter names contain spaces; the server needs %20, never '+' (HEMDP0119E otherwise)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.transport import build_url  # noqa: E402


class EncodingTest(unittest.TestCase):
    def test_spaces_are_percent20(self):
        u = build_url("https://h/x", {"param_filter": "2G_Marco:group:Tech-SMC Cairo Access Service Front Desk"})
        self.assertIn("Tech-SMC%20Cairo%20Access%20Service%20Front%20Desk", u)
        self.assertNotIn("+", u)


if __name__ == "__main__":
    unittest.main()
