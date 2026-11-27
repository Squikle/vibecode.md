"""friction.py over a tiny opencode database built here: `python -I -X utf8 -m unittest discover -s tools/work/tests`."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "friction.py"
RG = "rg : The term 'rg' is not recognized as the name of a cmdlet, function, script file, or operable program."


def shell(cmd, out):
    return {"type": "tool", "name": "shell", "state": {"status": "completed", "input": {"command": cmd},
                                                      "content": [{"type": "text", "text": out}]}}


def text(t):
    return {"type": "text", "text": t}


class Friction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "opencode.db"
        now = int(time.time() * 1000)
        parts = {  # session: [(message role, part)]
            "s1": [("assistant", shell("rg -n foo src", RG)),
                   ("assistant", shell("git rev-parse --short x", "fatal: Needed a single revision\n    except OSError:")),
                   ("assistant", shell("python -m pytest tests", "FAILED tests/test_a.py::test_a - assert 1 == 2"))],
            "s2": [("assistant", shell("rg foo", RG)),
                   ("assistant", shell("git rev-parse y", "fatal: Needed a single revision")),
                   ("assistant", shell("python -m pytest tests", "FAILED tests/test_a.py::test_a - assert 1 == 2"))],
            "s3": [("assistant", shell("rg bar", RG)),
                   ("assistant", shell("python -m pytest tests", "FAILED tests/test_a.py::test_a - assert 1 == 2")),
                   ("assistant", text("Done.\n\nfriction: function R shadowed by alias x2"))],
            "s4": [("assistant", text("All green.\n\nfriction: jq is not recognized x3")),
                   ("user", text("friction: jq is not recognized x9"))],  # a brief quoting the rule: not a report
            "root": [("assistant", shell("rg baz", RG))],  # the owner's session: only with --all
        }
        db = sqlite3.connect(self.db)
        db.executescript("create table session_v2 (id text, parent_id text, time_updated integer);"
                         "create table session_message (id text, session_id text, type text);"
                         "create table session_part (id text, message_id text, session_id text, ordinal integer,"
                         " type text, time_created integer, data text);")
        for sid, rows in parts.items():
            db.execute("insert into session_v2 values (?, ?, ?)", (sid, None if sid == "root" else "root", now))
            for i, (role, p) in enumerate(rows):
                mid = f"{sid}-m{i}"
                db.execute("insert into session_message values (?, ?, ?)", (mid, sid, role))
                db.execute("insert into session_part values (?, ?, ?, ?, ?, ?, ?)",
                           (f"{mid}-p", mid, sid, i, p["type"], now + i, json.dumps(p)))
        db.commit()
        db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args):
        done = subprocess.run([sys.executable, "-I", "-X", "utf8", str(TOOL), "--db", str(self.db), "--since", "1h",
                               "--promote", "2", *args], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.stderr, "")
        return done.returncode, done.stdout

    def row(self, out, needle):
        rows = [ln for ln in out.splitlines() if needle in ln and ln.startswith("|")]
        self.assertEqual(len(rows), 1, out)
        return [c.strip() for c in rows[0].strip("|").split("|")]

    def test_known_trap_counts_subagents_only(self):
        _, out = self.run_tool()
        self.assertEqual(self.row(out, "rg-missing")[:3], ["3", "3", "PROMOTE NEW"])
        self.assertIn("# Friction: 4 sessions (subagents", out)
        _, out = self.run_tool("--all")
        self.assertEqual(self.row(out, "rg-missing")[:2], ["4", "4"])

    def test_unknown_cluster_ranked_and_code_lines_skipped(self):
        _, out = self.run_tool()
        self.assertEqual(self.row(out, "Needed a single revision")[:3], ["2", "2", "PROMOTE NEW"])
        self.assertNotIn("except OSError", out)

    def test_product_test_failure_is_never_promoted(self):
        _, out = self.run_tool()
        self.assertNotIn("test_a", out.split("Tests (work")[0])
        self.assertIn("Tests (work, not friction): 1 clusters, 3 lines, 3 sessions.", out)

    def test_self_report_adds_hits_from_assistant_text_only(self):
        _, out = self.run_tool()
        self.assertEqual(self.row(out, "jq is not recognized")[:2], ["1", "3 (3 self)"])
        self.assertEqual(self.row(out, "pwsh-alias-shadow")[:2], ["1", "2 (2 self)"])

    def test_promote_alerts_once_with_state(self):
        state = self.dir / "state" / "friction.json"
        rc, out = self.run_tool("--state", str(state))
        self.assertEqual(rc, 1)
        self.assertIn("NEW PROMOTE: 2:", out)
        self.assertTrue(state.is_file())
        rc, out = self.run_tool("--state", str(state))
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.row(out, "rg-missing")[2], "PROMOTE")
        self.assertNotIn("NEW", out)


if __name__ == "__main__":
    unittest.main()
