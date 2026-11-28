"""role-telemetry.py over a tiny opencode database built here (no real sessions):
`python -I -X utf8 -m unittest discover -s tools/work/tests`."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "role-telemetry.py"
sys.path.insert(0, str(TOOL.parent))
import role_data  # noqa: E402

HOME = f"C:/w/{role_data.main_work(TOOL.parents[2]).parent.name}"  # this project's folder (roles.json `projects`)


def shell(cmd):
    return {"type": "tool", "name": "shell", "state": {"status": "completed", "input": {"command": cmd}}}


def read(path, **rng):
    return {"type": "tool", "name": "read", "state": {"status": "completed", "input": {"path": path, **rng}}}


DOCS_GREP = "git grep -n minimap -- docs/"
SESSIONS = {  # id: (agent, title, brief, [parts])
    "s-prod": ("product", "", "go", [shell(DOCS_GREP)] * 3 + [shell("python -I -X utf8 tools/work/find.py minimap")]),
    "s-plan": ("planner", "", "go", [read("docs/DECISIONS.md"), read("docs/DECISIONS.md", offset=10, limit=20)]),
    "s-lane": ("general", "", "You are executor E1 of p. Read AGENTS.md and docs/workflow/lane-brief.md", [
        shell('git grep -n "function mapContainer" -- src'),
        shell("Get-Content docs/PLANNING.md"),
        shell("Get-Content docs/PLANNING.md -TotalCount 20"),
        shell("Get-Content src/model/minimap.ts | Select-String class"),   # a class word in another segment: no match
        shell("python -c \"print(1)\""),
        shell("Start-Sleep 30"),
        read("C:/w/p-wt/e1/src/model/minimap.ts"), read("C:/w/p-wt/e1/src/ui/far.ts"),
        read("C:/w/p-wt/e1/AGENTS.md"), read("C:/w/p-wt/e1/docs/workflow/lane-brief.md")]),
    "s-arch": ("general", "", "You are the architect of p. Read AGENTS.md and docs/workflow/architect.md", []),
    "s-cto": ("general", "CTO2 gate", "hello", [shell("git worktree list")]),
    "s-coord": ("general", "", "You are the coordinator of p", [shell("git worktree list")]),
    "s-none": ("general", "misc", "hi", [shell("ls")]),
}


def build_db(path, sessions, now):
    db = sqlite3.connect(path)
    db.executescript("create table session_v2 (id text, parent_id text, agent text, title text, directory text,"
                     " time_updated integer);"
                     "create table session_message (id text, session_id text, type text, seq integer,"
                     " time_created integer, data text);"
                     "create table session_part (id text, message_id text, session_id text, type text,"
                     " time_created integer, data text);")
    for sid, (agent, title, brief, parts) in sessions.items():
        db.execute("insert into session_v2 values (?, 'root', ?, ?, ?, ?)", (sid, agent, title, HOME, now))
        db.execute("insert into session_message values (?, ?, 'user', 0, ?, ?)",
                   (f"{sid}-u", sid, now, json.dumps({"text": brief})))
        for i, p in enumerate(parts):
            db.execute("insert into session_part values (?, ?, ?, ?, ?, ?)",
                       (f"{sid}-p{i}", f"{sid}-a", sid, p["type"], now + i, json.dumps(p)))
    db.commit()
    db.close()


class RoleTelemetry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "opencode.db"
        self.now = int(time.time() * 1000)
        build_db(self.db, SESSIONS, self.now)
        self.seen = self.dir / "seen"
        self.seen.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args):
        done = subprocess.run([sys.executable, "-I", "-X", "utf8", str(TOOL), "--db", str(self.db), "--since", "1h",
                               "--seen-dir", str(self.seen), *args], capture_output=True, text=True, encoding="utf-8")
        return done.returncode, done.stdout + done.stderr

    def roles(self, *args):
        rc, out = self.run_tool("--json", *args)
        return rc, json.loads(out)

    def test_roles_by_agent_alias_brief_title(self):
        _, o = self.roles()
        r = o["roles"]
        self.assertEqual(r["product"]["sessions"], 2)          # `product`, and `planner` by its old name
        self.assertEqual(r["builder"]["sessions"], 1)         # the lane brief
        self.assertEqual(r["architect"]["sessions"], 1)        # the old CTO title tag; a session with no call never counts
        self.assertEqual(r["coordinator"]["sessions"], 1)
        self.assertEqual(r["other"]["sessions"], 1)
        self.assertNotIn("cto", r)

    def test_classification(self):
        _, o = self.roles()
        lane, prod = o["roles"]["builder"], o["roles"]["product"]
        self.assertEqual(lane["rules"], {"code-def-grep": 1, "doc-whole-read": 1, "polling": 1})
        self.assertEqual(lane["adhoc"], 1)
        self.assertEqual(prod["rules"], {"docs-grep": 3, "searched-doc-whole": 1})   # a ranged read is fine
        self.assertEqual(prod["role_tool"], 1)
        self.assertEqual(prod["share"], 0.8)                                       # 4 of its 5 calls
        self.assertEqual(o["roles"]["coordinator"]["rules"], {"status-sweep": 1})  # a coordinator-only rule
        self.assertEqual(o["roles"]["other"]["rules"], {})

    def test_flags_and_state_alert_once(self):
        rc, out = self.run_tool("--flag-min", "2")
        self.assertEqual(rc, 1, out)
        self.assertIn("- NEW product: 4 replaceable calls (80% of its shell calls); top docs-grep x3 -> find.py", out)
        state = self.dir / "state.json"
        rc, out = self.run_tool("--flag-min", "2", "--state", str(state))
        self.assertEqual(rc, 0, out)                                               # the first run is the baseline
        self.assertNotIn("replaceable calls", out)
        more = dict(SESSIONS)
        more["s-prod2"] = ("product", "", "go", [shell(DOCS_GREP)] * 6 + [shell("git grep -n x -- docs/BACKLOG.md")])
        self.db.unlink()
        build_db(self.db, more, self.now)
        log = self.dir / "log" / "tooluse.md"
        rc, out = self.run_tool("--flag-min", "2", "--state", str(state), "--log", str(log))
        self.assertEqual(rc, 1, out)
        self.assertIn("- NEW product: 11 replaceable calls", out)
        line = log.read_text(encoding="utf-8").splitlines()[0]
        self.assertRegex(line, r"^\d{4}-\d\d-\d\d \d\d:\d\d TOOLUSE flag=share role=product rule=docs-grep n=11 "
                               r"share=0\.\d+ hint=\"find\.py <term> / kmap\.py <topic>\"$")
        rc, out = self.run_tool("--flag-min", "2", "--state", str(state))
        self.assertEqual(rc, 0, out)                                               # alerted once
        self.assertIn("- product: 11 replaceable calls", out)

    def test_inline_helpers_and_silent_roles(self):
        more = {f"s-h{i}": ("general", "", "You are executor E1", [shell("function cut($s) { $s }; cut 1")] * 4)
                for i in range(3)}
        more.update({f"s-q{i}": ("general", "", "You are the librarian of p", [shell("ls")]) for i in range(3)})
        self.db.unlink()
        build_db(self.db, more, self.now)
        rc, out = self.run_tool()
        self.assertEqual(rc, 1, out)
        self.assertIn("| `cut` | 12 | 3 | builder |", out)
        self.assertIn("NEW inline helper `cut` typed 12 times in 3 sessions (builder): a missing tool?", out)
        self.assertIn("NEW librarian: 3 sessions and no tools/ script call at all", out)

    def test_offmap_reads_need_the_map(self):
        _, o = self.roles()
        self.assertEqual(o["roles"]["builder"]["mapped_sessions"], 0)             # it never asked kmap
        (self.seen / "s-lane.json").write_text(json.dumps({"paths": ["src/model/minimap.ts"]}), encoding="utf-8")
        _, o = self.roles()
        lane = o["roles"]["builder"]
        self.assertEqual((lane["reads"], lane["offmap"]), (2, 1))                  # start reads never count

    def test_examples_and_bad_args(self):
        rc, out = self.run_tool("--examples", "planner", "searched-doc-whole")
        self.assertEqual(rc, 0, out)
        self.assertIn("# product / searched-doc-whole: 1 examples (of 1)", out)
        self.assertIn("- `docs/DECISIONS.md`", out)
        rc, out = self.run_tool("--sinse", "1h")
        self.assertEqual(rc, 2, out)


if __name__ == "__main__":
    unittest.main()
