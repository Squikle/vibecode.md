"""`role-telemetry.py`'s per-session numbers and value half over a tiny database built here: a subscription session
priced at list rates (never $0), lookups against kmap calls, a big whole read and an error loop flagged, off-map
links appended once in the shared candidates contract, reviews read into spec quality, and each role rated
(`unknown` on thin data, never a guess) into the report and the vault note.
`python -I -X utf8 -m unittest discover -s tools/work/tests`."""
import contextlib
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import shutil
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
TOOL = HERE / "role-telemetry.py"
sys.path.insert(0, str(HERE))
import role_data  # noqa: E402
import role_value  # noqa: E402
import telemetry_sessions  # noqa: E402

REG = json.loads((HERE / "roles.json").read_text(encoding="utf-8"))
NAME = role_data.main_work(ROOT).parent.name  # this project's name, as roles.json `projects` takes it by default

COLS = ("id text, parent_id text, agent text, title text, directory text, time_created integer, time_updated integer,"
        " model text, cost real, tokens_input integer, tokens_output integer, tokens_reasoning integer,"
        " tokens_cache_read integer, tokens_cache_write integer")


def tool(name, status="completed", error=None, **inp):
    st = {"status": status, "input": inp}
    if error:
        st["error"] = {"type": "x", "message": error}
    return {"type": "tool", "name": name, "state": st}


def build(path, big):
    db = sqlite3.connect(path)
    db.executescript(f"create table session_v2 ({COLS});"
                     "create table session_message (id text, session_id text, type text, seq integer,"
                     " time_created integer, data text);"
                     "create table session_part (message_id text, session_id text, ordinal integer, type text,"
                     " time_created integer, data text);")
    now = int(time.time() * 1000)
    lane = [tool("shell", command="git grep -n foo -- src")] * 15 + [
        tool("read", path=str(big)), tool("read", path=f"C:/p/{NAME}-wt/x/docs/DESIGN.md"),
        tool("read", path=f"C:/p/{NAME}-wt/x/docs/DESIGN.md"),
        tool("shell", "error", "boom 1", command="git frob 1"), tool("shell", "error", "boom 2", command="git frob 2")]
    opus = '{"providerID":"anthropic","id":"claude-opus-5-5"}'
    rows = {"s-cl": ("general", f"You are **ABC**, a builder lane on {NAME}.",
                     '{"providerID":"claude-agent","id":"opus-lane"}', 0, (1_000_000, 100_000, 0, 10_000_000, 200_000),
                     lane),
            "s-api": ("reviewer", f"You review one batch of {NAME}.", opus, 3.0, (1, 1, 0, 1, 1),
                      [tool("shell", command="python -I -X utf8 tools/kmap/kmap.py abc --level 3")]),
            "s-help": ("helper", "Answer this question: where is crumbScale used?", '{"providerID":"claude-agent",'
                       '"id":"haiku"}', 0, (1000, 100, 0, 0, 1000), [tool("shell", command="git grep -n crumbScale")]),
            # not this project's (its folder and brief say nothing of it), however much it looks like a role
            "s-out": ("builder", "Fix my bike's gears.", opus, 50.0, (1, 1, 0, 1, 1),
                      [tool("shell", command="git grep -n bike")] * 20),
            # the coordinator's spawn preamble alone (a path into this project's .work) never makes a session ours
            "s-pre": ("general", f"You are TALK, a writer. Progress: C:\\p\\{NAME}\\.work\\talk\\progress.md", opus,
                      7.0, (1, 1, 0, 1, 1), [])}
    parent = {"s-help": "s-cl"}
    for sid, (agent, brief, model, cost, tok, parts) in rows.items():
        db.execute("insert into session_v2 values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",  # the home folder
                   (sid, parent.get(sid), agent, sid.upper(), str(pathlib.Path.home()), now, now, model, cost, *tok))
        db.execute("insert into session_message values (?, ?, 'user', 0, ?, ?)",
                   (sid + "-u", sid, now, json.dumps({"text": brief})))
        db.execute("insert into session_message values (?, ?, 'assistant', 1, ?, ?)",
                   (sid + "-a", sid, now, json.dumps({"tokens": {"input": 4, "cache": {"read": 0, "write": 9000}}})))
        for i, p in enumerate(parts):
            db.execute("insert into session_part values (?, ?, ?, 'tool', ?, ?)",
                       (sid + "-a", sid, i, now + i, json.dumps(p)))
    db.commit()
    db.close()


class Value(unittest.TestCase):
    def setUp(self):
        self.env = env = pathlib.Path(tempfile.mkdtemp())
        big = env / "big.md"
        big.write_text("x" * 60 * 1024, encoding="utf-8")
        build(env / "o.db", big)
        seen = env / "seen"
        seen.mkdir()
        (seen / "s-cl.json").write_text(json.dumps({"asks": {"spec:abc#3": 2, "dec:9#1": 1},
                                                    "paths": ["docs/specs/abc.md"]}), encoding="utf-8")
        work = env / "work" / "abc"
        work.mkdir(parents=True)
        (work / "review.md").write_text("# Review: `abc` (head 1, base 2)\n\n**Verdict: CHANGES NEEDED**\n\n"
                                        "1. **major [spec-gap]** the spec said nothing\n"
                                        "2. minor [no-test] nothing caught it\n", encoding="utf-8")
        (work / "review-2.md").write_text("# Review: `abc` round 2\n\n**Verdict: GREEN WITH NOTES**\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.env, ignore_errors=True)

    def run_tool(self, *args, tool=TOOL):
        done = subprocess.run([sys.executable, "-I", "-X", "utf8", str(tool), "--db", str(self.env / "o.db"), *args],
                              capture_output=True, text=True, encoding="utf-8", timeout=120)
        return done.returncode, done.stdout + done.stderr

    def tel(self, *args):
        return self.run_tool("--since", "1h", "--seen-dir", str(self.env / "seen"), *args)

    def test_sessions_priced_and_flagged(self):
        rc, out = self.tel("--json", "--out", str(self.env / "out"))
        js = json.loads(out)
        s = {r["id"]: r for r in js["sessions"]}
        self.assertEqual((s["s-cl"]["usd"], s["s-cl"]["kind"], s["s-cl"]["role"]), (9.0, "list", "builder"))  # 4+2+2+1
        self.assertEqual((s["s-api"]["usd"], s["s-api"]["kind"], s["s-api"]["kmap"]), (3.0, "db", 1))
        self.assertEqual((s["s-cl"]["search"], s["s-cl"]["kmap"], s["s-cl"]["first"], s["s-cl"]["errors"]),
                         (15, 0, 9004, 2))
        self.assertEqual(s["s-cl"]["big_reads"], [[str(self.env / "big.md").replace("\\", "/"), 60]])
        self.assertEqual({f["key"].split(":")[0] for f in js["session_flags"]}, {"lookups", "bigread", "errloop"})
        day = time.strftime("%Y-%m-%d")
        self.assertTrue((self.env / "out" / f"{day}.md").is_file())
        self.assertTrue(json.loads((self.env / "out" / f"{day}.json").read_text(encoding="utf-8")))

    def test_offmap_candidates_append_once(self):
        cands = self.env / "kmap" / "candidates.jsonl"
        for _ in range(2):
            self.tel("--candidates", str(cands))
        lines = [json.loads(x) for x in cands.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(len(lines) == 1 and sorted(lines[0]) == ["a", "at", "b", "evidence", "kind"], lines)
        self.assertEqual({k: lines[0][k] for k in ("a", "b", "kind", "evidence")},
                         {"a": "spec:abc", "b": "docs/DESIGN.md", "kind": "off-map-read", "evidence": "s-cl"})

    def test_projects_of_the_edge_cases(self):
        reg = self.env / "roles.json"
        reg.write_text(json.dumps({"projects": {"exclude": [{"title": "^PROBE\\b", "project": "other"}]}}),
                       encoding="utf-8")
        rules = role_data.project_rules(reg)
        for title, folder, brief, want in (
                ("a chat of the owner's", "C:/Windows/System32", None, "other"),
                ("a lane of another app", "C:/p/other-app", None, "other"),
                ("a lane here", f"C:/p/{NAME}-wt/x", None, "this"),
                ("PROBE p-split 1", f"C:/p/{NAME}-wt/x", None, "other"),        # tooling, though in this folder
                ("a spawn", "D:/elsewhere", f"You review one batch of {NAME}.", "this")):
            s = {"id": "x", "title": title, "directory": folder, "brief": brief, "parent": None}
            self.assertEqual(role_data.project_of(s, rules=rules), want, title)

    def test_a_candidate_names_the_file_in_the_repo(self):
        for path, want in ((str(ROOT / "tools" / "work" / "roles.py"), "tools/work/roles.py"),  # this checkout
                           ("C:/p/x-wt/other/tools/work/test_x.py", "tools/work/test_x.py"),    # another worktree's
                           ("C:/elsewhere/notes.md", "C:/elsewhere/notes.md")):                # outside: as read
            self.assertEqual(telemetry_sessions.rel(path, ROOT), want, path)

    def test_value_rating_and_vault_note(self):
        rc, out = self.tel("--value", "--work", str(self.env / "work"), "--vault", str(self.env / "vault"))
        self.assertIn("## Rating: is each role worth its cost?", out)
        self.assertIn("## Spec quality per week", out)
        note = (self.env / "vault" / "live" / "Roles.md").read_text(encoding="utf-8")
        b, a = REG["roles"]["builder"], REG["roles"]["architect"]
        cap = b["budget"]["week"]
        self.assertIn(f"| builder | {b['mode']} | 1 | 0 | 9 | {9 / cap:.0%} of ${cap:g} |", note)  # API $0, $9 list
        self.assertIn(f"| architect | {a['mode']} | 0 | 0 | 0 |", note)
        self.assertIn("**unknown**", note)
        self.assertIn(f"| builder | {time.strftime('%G-W%V')} | 1 | 1 | 1.00 |", out)  # its helper child counts

    def test_each_session_gets_its_project_and_only_this_counts(self):
        rc, out = self.tel("--json", "--value", "--work", str(self.env / "work"))
        js = json.loads(out)
        project = {r["id"]: r["project"] for r in js["sessions"]}  # every project is shown
        self.assertEqual(project, {"s-cl": "this", "s-api": "this", "s-help": "this", "s-out": "other",
                                   "s-pre": "other"})
        self.assertEqual({r["id"]: r["role"] for r in js["sessions"]}["s-out"], "other")  # its calls count as other's
        self.assertAlmostEqual(js["projects"]["other"]["api"], 57.0)
        rating = {r["role"]: r for r in js["value"]["rating"]}
        self.assertEqual((rating["builder"]["runs"], rating["builder"]["api_usd"]), (1, 0))  # not s-out's $50
        rc, out = self.run_tool("show", tool=HERE / "roles.py")
        row = next(line for line in out.splitlines() if line.startswith("| builder |"))
        self.assertIn("| 1 | 0 | 9 |", row)  # roles.py show (and may)

    def test_a_review_read(self):
        p = self.env / "lane" / "review-3.md"
        p.parent.mkdir()
        p.write_text("From: reviewer, batch crumb-sync (the owner). Branch crumb-sync, base 1.\n\n"
                     "# Verdict: CHANGES NEEDED\n\n1. **major [spec-gap]** a\n2. **blocker [no-test]** b\n3. nit: c\n",
                     encoding="utf-8")
        got = role_value.parse_review(p)
        self.assertEqual((got["batch"], got["verdict"], got["majors"]), ("crumb-sync", "changes", 2))
        self.assertEqual(got["findings"], [("major", "spec-gap"), ("blocker", "no-test")])

    def test_verdicts(self):
        for val, usd, mode, want in ((None, 10, "on", "unknown"), (0.1, 10, "on", "keep"), (0.1, 500, "on", "trim"),
                                     (0.5, 500, "on", "cut candidate"), (0.5, 10, "on", "trim"),
                                     (0.1, 10, "off", "off"),
                                     (0.24, 10, "on", "unknown")):  # 0.24 against 0.22 at n=50: noise
            reg = {"roles": {"x": {"mode": mode, "budget": {"run": 1, "week": 100},
                                   "value": [{"metric": "spec_gap_share", "threshold": 0.22, "good": "down"}]}}}
            m = {"spec_gap_share": (val, 50, ""), "_spend": {"x": [3, usd]}}
            self.assertEqual(role_value.rate(reg, m)[0][-1], want, (val, usd, mode))


if __name__ == "__main__":
    unittest.main()
