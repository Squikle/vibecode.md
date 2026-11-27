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
MIN = 60 * 1000


def shell(cmd, out, code=1):
    return {"type": "tool", "name": "shell", "state": {"status": "completed", "input": {"command": cmd},
                                                      "metadata": {"exit": code},
                                                      "content": [{"type": "text", "text": out}]}}


def failed_tool(name, error):
    return {"type": "tool", "name": name, "state": {"status": "error", "input": {"path": "a.py"}, "error": error}}


def text(t):
    return {"type": "text", "text": t}


class Friction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "opencode.db"
        self.now = now = int(time.time() * 1000)
        parts = {  # session: [(message role, part, minutes ago)]
            "s1": [("assistant", shell("rg -n foo src", RG), 30),
                   ("assistant", shell("git rev-parse --short x", "fatal: Needed a single revision\n    except OSError:",
                                       128), 0),
                   ("assistant", shell("python -m pytest tests", "FAILED tests/test_a.py::test_a - assert 1 == 2"), 0)],
            "s2": [("assistant", shell("rg foo", RG), 0),
                   ("assistant", shell("git rev-parse y", "fatal: Needed a single revision", 128), 0)],
            "s3": [("assistant", shell("rg bar", RG), 0),
                   ("assistant", text("Done.\n\nfriction: function R shadowed by alias x2"), 0)],
            "s4": [("assistant", text("All green.\n\nfriction: jq is not recognized x3"), 0),
                   ("user", text("friction: jq is not recognized x9"), 0)],  # a brief quoting the rule: not a report
            # Reads and test runs print other programs' errors: none of these is the lane's friction.
            "s5": [("assistant", shell("Get-Content .work/x/gate.log -Tail 40", "Error: element(s) not found", 0), 0),
                   ("assistant", shell("Select-String -Path e2e.log -Pattern Error",
                                       "TypeError: Cannot read properties of undefined (reading 'bounds')", 0), 0),
                   ("assistant", shell("npx playwright test smoke", "Error: No tests found.", 1), 0),
                   ("assistant", shell("python tools/ci/gate.py --full", "Error: 242 frames, 0 moves in 3 s", 1), 0),
                   ("assistant", shell("python tools/report.py", "ValueError: printed, but the run worked", 0), 0)],
            # The command's own failure counts, a reader's or a test run's included.
            "s6": [("assistant", shell("Get-Content .work/x/missing.log",
                                       "Get-Content: Cannot find path 'D:\\w\\missing.log' because it does not exist.",
                                       1), 0),
                   ("assistant", shell("python tools/mk.py", "ValueError: bad config key 'x'", 1), 0),
                   ("assistant", shell('$a = 1; "$a: x" | | Out-Null',
                                       "ParserError: \n     |  2 | x\n     |    ~\n"
                                       "     | An empty pipe element is not allowed.", 1), 0),
                   ("assistant", failed_tool("edit", "Could not find oldString in the file. It must match exactly."), 0),
                   ("assistant", shell("python -m pytest tests -q",
                                       "PermissionError: [WinError 32] The process cannot access the file because it "
                                       "is being used by another process: 'D:\\t\\pytest-of-u\\pytest-current'", 1), 0)],
            "root": [("assistant", shell("rg baz", RG), 0)],  # the owner's session: only with --all
        }
        db = sqlite3.connect(self.db)
        db.executescript("create table session_v2 (id text, parent_id text, time_updated integer);"
                         "create table session_message (id text, session_id text, type text);"
                         "create table session_part (id text, message_id text, session_id text, ordinal integer,"
                         " type text, time_created integer, data text);")
        for sid, rows in parts.items():
            db.execute("insert into session_v2 values (?, ?, ?)", (sid, None if sid == "root" else "root", now))
            for i, (role, p, ago) in enumerate(rows):
                mid = f"{sid}-m{i}"
                db.execute("insert into session_message values (?, ?, ?)", (mid, sid, role))
                db.execute("insert into session_part values (?, ?, ?, ?, ?, ?, ?)",
                           (f"{mid}-p", mid, sid, i, p["type"], now - ago * MIN + i, json.dumps(p)))
        db.commit()
        db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args, rc_ok=True):
        done = subprocess.run([sys.executable, "-I", "-X", "utf8", str(TOOL), "--db", str(self.db), "--since", "1h",
                               "--promote", "2", *args], capture_output=True, text=True, encoding="utf-8")
        if rc_ok:
            self.assertEqual(done.stderr, "")
        return done.returncode, done.stdout + done.stderr

    def row(self, out, needle):
        rows = [ln for ln in out.splitlines() if needle in ln and ln.startswith("|")]
        self.assertEqual(len(rows), 1, out)
        return [c.strip() for c in rows[0].strip("|").split("|")]

    def test_known_trap_counts_subagents_only(self):
        _, out = self.run_tool()
        self.assertEqual(self.row(out, "rg-missing")[:3], ["3", "3", "PROMOTE NEW"])
        self.assertIn("# Friction: 6 sessions (subagents", out)
        _, out = self.run_tool("--all")
        self.assertEqual(self.row(out, "rg-missing")[:2], ["4", "4"])

    def test_missing_ref_is_a_trap_and_code_lines_skipped(self):
        _, out = self.run_tool()
        self.assertEqual(self.row(out, "git-missing-ref")[:3], ["2", "2", "PROMOTE NEW"])
        self.assertNotIn("except OSError", out)

    def test_reads_and_test_runs_count_no_printed_errors(self):
        _, out = self.run_tool()
        for line in ("element(s) not found", "bounds", "No tests found", "242 frames", "test_a", "printed, but"):
            self.assertNotIn(line, out)
        self.assertIn("Tests (work, not friction): 0 clusters", out)

    def test_own_failures_count_with_their_trap(self):
        _, out = self.run_tool()
        self.assertEqual(self.row(out, "guessed-path")[:2], ["1", "1"])  # a read that failed on its own
        self.assertEqual(self.row(out, "bad config key")[:2], ["1", "1"])  # non-zero exit, not a reader
        self.assertEqual(self.row(out, "pwsh-parse")[:2], ["1", "1"])
        self.assertEqual(self.row(out, "edit-stale")[:2], ["1", "1"])
        self.assertEqual(self.row(out, "pytest-temp-race")[:2], ["1", "1"])  # a test run's own failure

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

    def test_fixed_trap_shows_hits_since_and_never_promotes(self):
        state = self.dir / "friction.json"
        date = time.strftime("%Y-%m-%dT%H:%M", time.localtime((self.now - 10 * MIN) / 1000))
        rc, out = self.run_tool("--state", str(state), "--fixed", f"rg-missing={date}")
        self.assertEqual(self.row(out, "rg-missing")[:3], ["3", "3", f"fixed {date}: 2 hits in 2 sessions since"])
        self.assertIn("NEW PROMOTE: 1: git-missing-ref", out)
        self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["fixed"], {"rg-missing": date})
        rc, out = self.run_tool("--state", str(state))  # the date is kept
        self.assertEqual(rc, 0, out)
        self.assertIn(f"fixed {date}", self.row(out, "rg-missing")[2])
        rc, out = self.run_tool("--fixed", "no-such-trap=2026-11-26", rc_ok=False)
        self.assertNotEqual(rc, 0)
        self.assertIn("unknown trap", out)


if __name__ == "__main__":
    unittest.main()
