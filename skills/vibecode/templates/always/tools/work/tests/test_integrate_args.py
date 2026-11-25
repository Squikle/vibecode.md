"""integrate.py parses its arguments before anything runs: `python -I -X utf8 -m unittest discover -s tools/work/tests`."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "integrate.py"


def call(*args):
    with tempfile.TemporaryDirectory() as tmp:  # no git repository: a git call would fail loudly
        return subprocess.run([sys.executable, "-I", "-X", "utf8", str(TOOL), *args], cwd=tmp, capture_output=True,
                              text=True, encoding="utf-8")


class Args(unittest.TestCase):
    def test_help_is_no_branch(self):
        done = call("-h")
        self.assertEqual(done.returncode, 0)
        self.assertIn("usage: integrate.py", done.stdout)
        self.assertNotIn("git", done.stderr)

    def test_missing_id_stops_first(self):
        done = call("lane/x")
        self.assertEqual(done.returncode, 2)
        self.assertIn("required: id", done.stderr)


if __name__ == "__main__":
    unittest.main()
