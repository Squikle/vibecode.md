"""doubt.py over a tiny opencode database and repo built here: `python -I -X utf8 -m unittest discover -s tools/work/tests`."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "doubt.py"
MIN = 60 * 1000
FILES = ["src/engine/a.py", "src/ui/b.ts", "src/c.py", "src/own.py", "src/engine/util.py", "web/src/engine/util.py",
         "docs/DECISIONS.md", "docs/specs/x.md", "docs/workflow/lane-brief.md", "docs/BACKLOG.md", "README.md"]
LANE = ("user", {"type": "text", "text": "Follow docs/workflow/lane-brief.md."})


def tool(name, **inp):
    return {"type": "tool", "tool": name, "state": {"status": "completed", "input": inp, "output": ""}}


def shell(cmd):
    return tool("shell", command=cmd)


def text(t):
    return {"type": "text", "text": t}


class Doubt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.repo = self.dir / "repo"
        for f in FILES:
            (self.repo / f).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / f).write_text("x\n", encoding="utf-8")
        self.db = self.dir / "opencode.db"
        self.now = now = int(time.time() * 1000)
        a_wt = "D:\\w\\proj-wt\\lane1\\src\\engine\\a.py"  # a worktree's path: the same repo file
        parts = {  # session: [(message role, part)]
            # Read, then the decisions within the window: one lookup, with the read's lines.
            "d1": [("assistant", tool("read", path="D:\\w\\proj-wt\\lane1\\docs\\workflow\\lane-brief.md")),
                   ("assistant", tool("read", path=a_wt, offset=100, limit=40)),
                   ("assistant", tool("read", path="D:/other/zzz.py")),  # another project's file: no row
                   ("assistant", tool("read", path="D:/w/proj-wt/lane1/docs/DECISIONS.md"))],
            # A grep on the file, then `git blame` on it.
            "d2": [LANE, ("assistant", tool("grep", pattern="clamp", path="src/engine/a.py")),
                   ("assistant", shell("git blame -L 120,130 -- src/engine/a.py"))],
            # An explicit doubt line; a Select-String on a code file, then a specs grep in the same command.
            "d3": [LANE, ("assistant", shell("Select-String -Path src/ui/b.ts -Pattern hop; git grep -e hop -- docs/specs")),
                   ("assistant", text("Done.\n\ndoubt: `src/engine/a.py:120` | why the double clamp?"))],
            # False alarms: a lane reading docs for its own task (named in its brief, or in its own diff).
            "d4": [LANE, ("user", text("Build batch x: fix src/own.py, the spec is docs/specs/x.md.")),
                   ("assistant", tool("read", path="src/own.py")),
                   ("assistant", tool("read", path="docs/specs/x.md")),
                   ("assistant", tool("read", path="src/ui/b.ts")),
                   ("assistant", tool("grep", pattern="hop", path="docs/specs")),
                   ("assistant", tool("edit", path="src/ui/b.ts", oldString="x", newString="y"))],
            # Out of the window: a docs read six tool calls after the code read; a brief quoting the rule.
            "d5": [LANE, ("user", text("doubt: src/c.py:3 | quoted from the brief, not a report")),
                   ("assistant", tool("read", path="src/c.py"))] +
                  [("assistant", shell(f"python tools/x.py {i}")) for i in range(5)] +
                  [("assistant", tool("read", path="docs/DECISIONS.md"))],
            # State, not a reason: the backlog, a grep over all of docs/ (a name search).
            "d6": [LANE, ("assistant", tool("read", path="src/c.py")),
                   ("assistant", tool("read", path="docs/BACKLOG.md")),
                   ("assistant", tool("read", path="src/c.py")),
                   ("assistant", tool("grep", pattern="hop", path="docs"))],
            # A reviewer (no lane brief) reads code and the decisions as its job: only with --roles.
            "rev": [("user", text("Review the diff of batch x.")),
                    ("assistant", tool("read", path="src/ui/b.ts")),
                    ("assistant", tool("read", path="docs/DECISIONS.md"))],
            "root": [("assistant", tool("read", path="src/engine/a.py")),  # the owner's session: only with --all
                     ("assistant", shell("git log -L 5,9:src/engine/a.py"))],
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
                           (f"{mid}-p", mid, sid, i, p["type"], now - MIN + i, json.dumps(p)))
        db.commit()
        db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args, rc_ok=True):
        done = subprocess.run([sys.executable, "-I", "-X", "utf8", str(TOOL), "--repo", str(self.repo), "--db",
                               str(self.db), "--since", "1h", *args], capture_output=True, text=True, encoding="utf-8")
        if rc_ok:
            self.assertEqual(done.stderr, "")
        return done.returncode, done.stdout + done.stderr

    def row(self, out, needle):
        rows = [ln for ln in out.splitlines() if f"`{needle}`" in ln and ln.startswith("|")]
        self.assertEqual(len(rows), 1, out)
        return [c.strip() for c in rows[0].strip("|").split("|")]

    def test_each_signal_counts_for_its_file(self):
        rc, out = self.run_tool()
        sessions, lookups, doubts, flag, _, lines, example = self.row(out, "src/engine/a.py")
        self.assertEqual((sessions, lookups, doubts, flag), ("3", "2", "1", "PROMOTE NEW"))
        for rng in ("L100-139", "L120-130", "L120"):
            self.assertIn(rng, lines)
        self.assertEqual(example, "why the double clamp?")
        self.assertEqual(self.row(out, "src/ui/b.ts")[:4], ["1", "1", "0", ""])  # d3 only: d4's is its own diff
        self.assertEqual(rc, 1)

    def test_own_task_lookups_are_dropped(self):
        _, out = self.run_tool()
        self.assertNotIn("src/own.py", out)
        self.assertIn("3 lookups (2 own-task dropped)", out)

    def test_out_of_window_brief_quotes_and_other_projects_count_nothing(self):
        _, out = self.run_tool()
        for name in ("src/c.py", "zzz.py", "quoted from the brief", "lane-brief"):
            self.assertNotIn(name, out)
        self.assertIn("# Doubt: 7 sessions (subagents, last 1h; 6 lanes)", out)

    def test_lanes_and_subagents_only_unless_roles_and_all(self):
        _, out = self.run_tool("--all")  # the root is no lane
        self.assertEqual(self.row(out, "src/engine/a.py")[:3], ["3", "2", "1"])
        _, out = self.run_tool("--roles")  # the reviewer counts
        self.assertEqual(self.row(out, "src/ui/b.ts")[:3], ["2", "2", "0"])
        _, out = self.run_tool("--all", "--roles")
        self.assertEqual(self.row(out, "src/engine/a.py")[:3], ["4", "3", "1"])  # root's `git log -L`
        self.assertIn("8 sessions (all, last 1h; every role)", out)

    def test_promote_alerts_once_with_state(self):
        state = self.dir / "state" / "doubt.json"
        rc, out = self.run_tool("--state", str(state))
        self.assertEqual(rc, 1)
        self.assertIn("NEW PROMOTE: 1: src/engine/a.py", out)
        rc, out = self.run_tool("--state", str(state))
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.row(out, "src/engine/a.py")[3], "PROMOTE")
        rc, _ = self.run_tool("--promote", "4")
        self.assertEqual(rc, 0)

    def test_a_directory_source_is_refused_without_an_export(self):
        empty = self.dir / "no-tools"
        empty.mkdir()
        done = subprocess.run([sys.executable, "-I", "-X", "utf8", str(TOOL), "--repo", str(self.repo), "."],
                              capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PATH": str(empty)},
                              cwd=self.dir)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("doubt: .: a directory; sources are session ids or export .json files; omit for the database",
                      done.stderr)
        self.assertNotIn("Traceback", done.stderr)


class Paths(unittest.TestCase):
    """A path anywhere (a worktree, a clone, relative) names the repo file whose tail it ends in."""
    def test_longest_tail_wins_and_a_guess_is_none(self):
        spec = importlib.util.spec_from_file_location("doubt", TOOL)
        d = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(d)
        with tempfile.TemporaryDirectory() as tmp:
            for f in FILES:
                (Path(tmp) / f).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / f).write_text("x\n", encoding="utf-8")
            repo = d.Repo(Path(tmp))
            self.assertEqual(repo.file("C:\\Wt\\L\\Web\\Src\\Engine\\util.py"), "web/src/engine/util.py")
            self.assertEqual(repo.file("/x/src/engine/util.py"), "src/engine/util.py")
            self.assertEqual(repo.file("util.py"), None)  # two of them: no guess
            self.assertEqual(repo.file("b.ts"), "src/ui/b.ts")
            self.assertEqual(repo.file("engine/a.py:12"), "src/engine/a.py")
            self.assertEqual(repo.file("D:/other/zzz.py"), None)
            self.assertEqual(repo.dir("D:\\w\\docs"), "docs")
            scan = d.Scan(repo)  # blame and log -L name their lines; a write names nothing
            for cmd in ("git log -L 5,9:src/engine/a.py", "git blame -L 5,+4 src/engine/a.py",
                        "Get-Content src/engine/a.py > src/c.py"):
                scan.part("s", shell(cmd))
            self.assertEqual(scan.by_sid["s"].events, [(1, "blame", "src/engine/a.py", "L5-9"),
                                                       (2, "blame", "src/engine/a.py", "L5-9")])


if __name__ == "__main__":
    unittest.main()
