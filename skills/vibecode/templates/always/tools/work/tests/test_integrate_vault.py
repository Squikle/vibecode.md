"""integrate.py builds the merged docs' vault before it commits: a miss stops it with nothing committed; fixed in the
work tree, the same call resumes. `python -I -X utf8 -m unittest discover -s tools/work/tests` (needs git)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "integrate.py"
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
       "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
TABLE = "# Decisions\n\n| # | Decision | Why | Where |\n|---|---|---|---|\n| 1 | a | b | c |\n"


class Vault(unittest.TestCase):
    def test_miss_stops_then_fix_resumes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # git's objects are read-only on Windows
            root = Path(tmp)

            def git(*a):
                return subprocess.run(["git", "-C", tmp, *a], capture_output=True, text=True, env=ENV, check=True).stdout

            def integrate():
                return subprocess.run([sys.executable, "-I", "-X", "utf8", str(TOOL), "lane/x", "x1"], cwd=tmp,
                                      capture_output=True, text=True, encoding="utf-8", env=ENV)

            git("init", "-q", "-b", "main")
            (root / "docs" / "specs").mkdir(parents=True)
            dec = root / "docs" / "DECISIONS.md"
            dec.write_text(TABLE, encoding="utf-8")
            (root / ".gitignore").write_text(".work/\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-qm", "start")
            git("checkout", "-qb", "lane/x")
            (root / "docs" / "specs" / "x1.md").write_text("# x1\n", encoding="utf-8")
            dec.write_text(TABLE + "| NEW | d | e | f | stray |\n", encoding="utf-8")  # a cell too many
            git("add", "-A")
            git("commit", "-qm", "x1")
            git("checkout", "-q", "main")
            report = root / ".work" / "x1" / "report.md"
            report.parent.mkdir(parents=True)
            report.write_text("## x1\n\n| `x1` | built | ok |\n", encoding="utf-8")

            done = integrate()
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            self.assertIn("VAULT MISS: docs/DECISIONS.md:6: 5 cells, the header has 4", done.stderr)
            self.assertIn("nothing committed", done.stderr)
            self.assertEqual(len(git("log", "--oneline").splitlines()), 1)

            dec.write_text(TABLE + "| NEW | d | e | f |\n", encoding="utf-8")
            git("add", "-A")
            done = integrate()
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("vault check: clean", done.stdout)
            self.assertIn("decision 2:", done.stdout)
            self.assertIn("Merge lane/x: x1.", git("log", "-1", "--format=%s"))


if __name__ == "__main__":
    unittest.main()
