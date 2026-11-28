"""order.py over a backlog written here: `python -I -X utf8 -m unittest discover -s tools/work/tests`."""
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "order.py"
BACKLOG = """# Backlog: open batches, in order

**Tiers:** speed last.

| # | Batch | What | Status | Waits on | Same files as | Spec |
|---|---|---|---|---|---|---|
| 1 | `alpha` First | a | ready to build | nothing | none | `docs/specs/alpha.md` |
| 2 | `beta` Second | b | ready to build | nothing | none | `docs/specs/beta.md` |
| 3  | `gamma` Third | c | needs grooming | `beta` | none | `docs/specs/gamma.md` |
| 4 | `delta` Fourth | d | ready to build | nothing | none | `docs/specs/delta.md` |

Text after the table stays.
"""


class Order(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs").mkdir()
        self.backlog = self.root / "docs/BACKLOG.md"
        self.backlog.write_bytes(BACKLOG.replace("\n", "\r\n").encode())    # a CRLF file stays CRLF
        self.log = self.root / "docs/log/order.md"

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args):
        done = subprocess.run([sys.executable, "-I", "-X", "utf8", str(TOOL), "--root", str(self.root), *args],
                              capture_output=True, text=True, encoding="utf-8")
        return done.returncode, done.stdout + done.stderr

    def ids(self):
        return re.findall(r"^\| (\d+) \| `(\w+)`", self.backlog.read_bytes().decode(), re.M)

    def test_top_moves_stamps_renumbers_and_logs(self):
        rc, out = self.run_tool("word", 'Do "delta" first, then gamma', "--rows", "delta,gamma", "--top")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.ids(), [("1", "delta"), ("2", "gamma"), ("3", "alpha"), ("4", "beta")])
        raw = self.backlog.read_bytes().decode()
        self.assertNotIn("\n\n", raw.replace("\r\n", "\r"))                      # no LF-only line crept in
        self.assertIn("| 1 | `delta` Fourth | d | ready to build (owner ", raw)
        self.assertIn("| 4 | `beta` Second | b | ready to build | nothing | none | `docs/specs/beta.md` |", raw)
        self.assertTrue(raw.endswith("Text after the table stays.\r\n"))
        line = self.log.read_text(encoding="utf-8").splitlines()[-1]
        self.assertRegex(line, r"^- \d{4}-\d\d-\d\d \d\d:\d\d WORD rows=delta,gamma move=top "
                               r"quote=\"Do 'delta' first, then gamma\"$")
        self.assertIn("-| 4 | `delta`", out)                                       # the diff is printed
        rc, out = self.run_tool("check")
        self.assertEqual(rc, 0, out)
        self.assertIn("order check: 0 red (4 rows, 2 rows with a word)", out)

    def test_after_hold_needs_status_drop(self):
        self.assertEqual(self.run_tool("word", "alpha after delta", "--rows", "alpha", "--after", "delta")[0], 0)
        self.assertEqual([i for _, i in self.ids()], ["beta", "gamma", "delta", "alpha"])
        self.assertEqual(self.run_tool("word", "beta: rethink the export", "--rows", "beta",
                                       "--needs", "product: CSV or JSON?")[0], 0)
        raw = self.backlog.read_bytes().decode()
        self.assertIn("| `beta` Second | b | held, needs product: CSV or JSON? (owner ", raw)
        self.assertIn('was="ready to build" needs="product: CSV or JSON?"', self.log.read_text(encoding="utf-8"))
        self.assertEqual(self.run_tool("check")[0], 0)
        self.assertEqual(self.run_tool("word", "beta is fine now", "--rows", "beta", "--status", "ready to build")[0], 0)
        self.assertIn("| `beta` Second | b | ready to build (owner ", self.backlog.read_bytes().decode())
        self.assertEqual(self.run_tool("word", "never gamma", "--rows", "gamma", "--drop")[0], 0)
        self.assertEqual([i for _, i in self.ids()], ["beta", "delta", "alpha"])
        rc, out = self.run_tool("check")
        self.assertEqual(rc, 0, out)

    def test_check_finds_a_row_moved_against_the_word(self):
        self.run_tool("word", "delta first", "--rows", "delta", "--top")
        self.run_tool("word", "alpha after beta", "--rows", "alpha", "--after", "beta")
        raw = self.backlog.read_bytes().decode()
        rows = [ln for ln in raw.split("\r\n") if ln.startswith("| ") and "`" in ln]
        new = raw.replace(rows[0], "| 1 | `epsilon` New | e | ready to build | nothing | none | |\r\n"
                          + rows[0].replace("| 1 |", "| 2 |", 1))                 # another session adds a row on top
        new = new.replace("| 2 | `beta`", "| 3 | `beta`").replace("| 3 | `alpha`", "| 4 | `alpha`")
        new = new.replace("| 4 | `gamma`", "| 5 | `gamma`")
        self.backlog.write_bytes(new.encode())
        rc, out = self.run_tool("check")
        self.assertEqual(rc, 1, out)
        self.assertIn("RED  `delta` came first by the owner's word, now under unstamped `epsilon`", out)
        self.backlog.write_bytes(BACKLOG.replace("| 3  | `gamma`", "| 7 | `gamma`").encode())   # a hand edit
        rc, out = self.run_tool("check")
        self.assertIn("RED  `alpha` goes after `beta` by the owner's word, now on the wrong side", out)
        self.assertIn("RED  `delta` came first by the owner's word, now under unstamped `alpha`", out)
        self.assertIn("RED  the # column is not 1..n in order (renumber)", out)

    def test_refuses_without_writing(self):
        before = self.backlog.read_bytes()
        rc, out = self.run_tool("word", "x", "--rows", "alpha,nope", "--top")
        self.assertEqual(rc, 1, out)
        self.assertIn("`nope` (0x)", out)
        rc, out = self.run_tool("word", "x", "--rows", "alpha", "--after", "alpha")
        self.assertEqual(rc, 2, out)
        rc, out = self.run_tool("word", "x", "--rows", "alpha", "--top", "--hold")
        self.assertEqual(rc, 2, out)                                                # one move at a time
        rc, out = self.run_tool("word", "x", "--rows", "alpha", "--top", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("would log:", out)
        rc, out = self.run_tool("word", "x", "--rows", "alpha", "--tpo")
        self.assertEqual(rc, 2, out)
        self.assertEqual(self.backlog.read_bytes(), before)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
