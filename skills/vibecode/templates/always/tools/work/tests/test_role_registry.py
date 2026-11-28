"""`tools/work/roles.py` and its registry `roles.json`: the agent files, `helper.md` and the command models match the
registry (`check`); `set` regenerates them; `may` says no for a role that is off or on-request without an ask, and
warns (or, in `block` mode, says no) over a week's budget; and each role's promises hold in its generated agent file,
read the way opencode reads it (the last matching rule wins), in both git forms.
`python -I -X utf8 -m unittest discover -s tools/work/tests`."""
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import roles  # noqa: E402
import role_data  # noqa: E402

TOOL = HERE / "roles.py"
TEMPLATE = ROOT.name == "always" and (ROOT.parent / "team").is_dir()  # the kit's own copy: its groups, unfilled
RULE = re.compile(r'\{action: "([^"]+)", resource: "([^"]*)", effect: (\w+)\}')
PREFIXES = ("", "C:/x/git.exe ")  # an `.exe` command (git, rg) has had text before it (roles.json `shell_note`)
CHAIN = re.compile(r"\|\||&&|[|;]")
DRIVEN = ("tools/work/roles.json", "tools/work/helper.md", ".opencode", "docs/workflow", "docs/PLANNING.md")
TREE = None  # what the registry drives: this checkout, or (in the kit) its groups merged
REG = {}
FILES = []


def setUpModule():
    global TREE, REG, FILES
    TREE = ROOT
    if TEMPLATE:
        TREE = Path(tempfile.mkdtemp())
        for g in ("always", "team", "reviewers"):
            for src in (ROOT.parent / g).rglob("*"):
                rel = src.relative_to(ROOT.parent / g)
                if src.is_file() and "__pycache__" not in rel.parts and rel.parts[0] in ("dot-opencode", "docs", "tools"):
                    rel = Path(".opencode", *rel.parts[1:]) if rel.parts[0] == "dot-opencode" else rel
                    (TREE / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, TREE / rel)
    REG = json.loads((TREE / "tools/work/roles.json").read_text(encoding="utf-8"))
    FILES = [n for n, r in REG["roles"].items() if r["agent"].get("file") and roles.present(TREE, r)]


def tearDownModule():
    if TEMPLATE:
        shutil.rmtree(TREE, ignore_errors=True)


def run(*args):
    done = subprocess.run([sys.executable, "-I", "-X", "utf8", str(TOOL), *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    return done.returncode, done.stdout + done.stderr


def rules_of(role):
    return RULE.findall((TREE / ".opencode" / "agents" / f"{role}.md").read_text(encoding="utf-8"))


def wild(pat):
    return re.compile("".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pat), re.I | re.S)


def decide(role, action, resource):
    """opencode's reading: the base policy allows, then every matching rule in order, the last one wins."""
    effect = "allow"
    for act, res, eff in rules_of(role):
        if wild(act).fullmatch(action) and wild(res).fullmatch(resource.replace("\\", "/")):
            effect = eff
    return effect


def parts(cmd):
    """The subcommands opencode checks one by one (measured: `|`, `;`, `&&` split), quotes kept whole."""
    out, cur, quote, i = [], "", "", 0
    while i < len(cmd):
        c = cmd[i]
        if quote:
            quote = "" if c == quote else quote
        elif c in "'\"":
            quote = c
        elif CHAIN.match(cmd, i):
            out.append(cur.strip())
            cur, i = "", i + len(CHAIN.match(cmd, i).group(0))
            continue
        cur += c
        i += 1
    return [p for p in out + [cur.strip()] if p]


def shell(role, cmd):
    """{allow} or {deny} (a set: one per prefix form): a chain is refused when any of its subcommands is."""
    return {"deny" if any(decide(role, "shell", p + part) == "deny" for part in parts(cmd)) else "allow"
            for p in PREFIXES}


def git_forms(verb_args):
    return [f"git {verb_args}", f"git -C C:/p/x-wt/y {verb_args}", f"git -C . {verb_args}"]


def present(*names):
    return [n for n in names if n in FILES]


CMD = re.compile(r"`((?:git|python3?|pwsh|npm|npx|docker|ssh|Select-String|Get-Content|rg) [^`]+)`")
NEG = re.compile(r"\b(never|don't|do not|no |not |refuse|forbid|instead of)\b", re.I)


def required(role):
    """The commands a role's own briefs tell it to run (backticked, a tool first), placeholders filled in; a command
    in a sentence that forbids it (never, not, ...) is skipped."""
    got = set()
    for rel in REG["roles"][role].get("briefs", []):
        p = TREE / rel
        for line in (p.read_text(encoding="utf-8").splitlines() if p.is_file() else []):
            for m in CMD.finditer(line):
                if not NEG.search(line[max(0, m.start() - 80):m.start()]):
                    got.add(re.sub(r"\[[^\]]*\]", "", re.sub(r"<[^>]*>", "x", m.group(1))).strip())
    return sorted(got)


class Rules(unittest.TestCase):
    def test_the_files_match_the_registry(self):
        rc, out = run("--registry", str(TREE / "tools/work/roles.json"), "check")
        self.assertEqual(rc, 0, out)

    def test_every_agent_keeps_the_ladder(self):
        self.assertIn("helper", FILES)
        for role in FILES:
            with self.subTest(role=role):
                text = (TREE / ".opencode" / "agents" / f"{role}.md").read_text(encoding="utf-8")
                self.assertNotIn("effect: ask", text)                       # nobody answers a spawned session
                for action in ("skill", "execute", "playwright_*", "serena_*", "generate_image"):
                    self.assertEqual(decide(role, action, "*"), "deny")
                for act, res, _ in rules_of(role):
                    if act == "shell" and res != "*":                       # a word is whole, never a substring:
                        for tok in res.split(" "):                          # no `*` touching a letter, but inside a
                            if re.search(r"\*[A-Za-z]|[A-Za-z]\*", tok):   # flag, a python or a file path
                                self.assertTrue(tok.startswith("-") or "python" in tok or "." in tok, (res, tok))
                r = REG["roles"][role]
                self.assertEqual(decide(role, "question", "*"), "allow" if r["agent"].get("question") else "deny")
                self.assertEqual(decide(role, "websearch", "x"),
                                 "allow" if role in ("architect", "product", "librarian") else "deny")
                self.assertIn(f"model: {roles.model_line(r)}", text)
                self.assertEqual("## Hand reads to `helper`" in text, role != "helper")  # every role hands reads over
                self.assertIn("**A refused call is not an answer.**", text)  # a refusal never reads as "not found"

    def test_who_never_pushes_resets_or_merges(self):
        for verbs, who in ((("push origin main", "push"), ("builder", "reviewer", "plan-review", "pregroomer", "helper")),
                           (("reset --hard HEAD~1",), ("reviewer", "plan-review", "pregroomer", "helper")),
                           # not the reviewer: its brief has it merge main before its verdict
                           (("merge main",), ("plan-review", "pregroomer", "helper"))):
            for role in present(*who):
                for cmd in [c for v in verbs for c in git_forms(v)]:
                    self.assertEqual(shell(role, cmd), {"deny"}, (role, cmd))

    def test_every_command_a_brief_requires_is_allowed(self):
        """A deny must never break what the role's own brief tells it to do (the reviewer's `git merge main`)."""
        for role in [r for r in FILES if REG["roles"][r].get("briefs")]:
            for cmd in required(role):
                self.assertEqual(shell(role, cmd), {"allow"}, (role, cmd))

    def test_git_bans_come_from_one_list(self):
        """Every banned git subcommand goes through `git_deny`, which writes both forms (`git <v>`, `git -C <p> <v>`)."""
        for name in FILES:
            a = REG["roles"][name]["agent"]
            self.assertFalse([p for p, _ in a.get("shell_deny", []) if "git " in p], name)
            for verb in a.get("git_deny", []):
                v = verb.replace("*", "x")
                self.assertEqual(shell(name, f"git -C C:/w {v} y"), {"deny"}, (name, verb))
                self.assertEqual(shell(name, f"git {v} y"), {"deny"}, (name, verb))

    def test_brief_bans_hold(self):
        bans = {"builder": ["pull", "config core.bare true", "worktree remove x", "checkout main", "switch main",
                            "clean -fdx"],
                "reviewer": ["worktree remove x"], "integrator": ["worktree remove x"]}
        for role in present(*bans):
            for cmd in [c for v in bans[role] for c in git_forms(v)]:
                self.assertEqual(shell(role, cmd), {"deny"}, (role, cmd))

    def test_real_lookups_run(self):
        lookups = ["rg -n parseConfig src", 'rg -n "a|b" docs', "git log --oneline -5",
                   "Select-String -Pattern model docs/ARCHITECTURE.md", "git -C C:/p/x-wt/y log --oneline -5 | Select-String Fix"]
        for role in FILES:
            for cmd in lookups:
                self.assertEqual(shell(role, cmd), {"allow"}, (role, cmd))
        for role in present("builder", "reviewer"):
            self.assertEqual(shell(role, "git merge main"), {"allow"}, role)  # a lane merges main when it must
        for cmd in ("git log -3", "git -C . status --short", "git -C x show HEAD:docs/a.md", "rg -n parse src",
                    "Get-Content docs/a.md -TotalCount 40", "python -I -X utf8 tools/kmap/kmap.py x --level 3"):
            self.assertEqual(shell("helper", cmd), {"allow"}, cmd)

    def test_helper_only_reads(self):
        for cmd in ("Set-Content a.md x", "Get-Content a.md > b.md", "python -I -c 1", "Remove-Item -Recurse x",
                    "git -C . checkout main", "git commit -m x", "npm ci",
                    # each subcommand of a chain is checked alone (measured), and every word is whole
                    "Get-Content a.md; npm ci", "git log -1 && git push", "git log | Out-File x.txt", "rg x | sh",
                    "Get-Content a.md; sh", "git log -1 && sh", "del x.txt", "iex x", "curl x",
                    "git clean -fdx -e org x", "npm ci --prefix rg x", "git stash", "git -C . restore src/a.ts",
                    "git branch -D main", "git -C . worktree add ../x", "python -I -c \"import os\" tools/work/map.py x"):
            self.assertEqual(shell("helper", cmd), {"deny"}, cmd)
        for path in ("src/a.ts", "C:/p/x/.work/x/review.md"):
            self.assertEqual(decide("helper", "edit", path), "deny")

    def test_no_climbing_out_of_an_edit_allowlist(self):
        for role in FILES:
            edit = REG["roles"][role]["agent"].get("edit")
            for allowed in (edit.get("only", []) if isinstance(edit, dict) else []):
                base = allowed.replace("*/", "C:/x/").replace("*", "a.md")
                self.assertEqual(decide(role, "edit", base), "allow", (role, base))
                self.assertEqual(decide(role, "edit", base.replace("a.md", "../../src/a.ts")), "deny", (role, base))

    def test_only_the_helper_has_a_shell_allowlist(self):
        for name in FILES:
            allows = [res for act, res, eff in rules_of(name) if act == "shell" and eff == "allow"]
            self.assertEqual(bool(allows), name == "helper", (name, allows))

    def test_the_spawned_briefs_point_at_helper(self):
        for role in present("builder", "reviewer", "integrator"):
            first = REG["roles"][role]["briefs"][0]
            self.assertIn("tools/work/helper.md", (TREE / first).read_text(encoding="utf-8"), first)

    def test_edits_stay_in_their_lane(self):
        for role, mine, theirs in (
                ("plan-review", [".work/plan/p1/plan-review.md", "C:/p/x/.work/plan/p1/plan-review.md"],
                 ["docs/specs/x.md", "C:/p/x-wt/y/docs/BACKLOG.md", "src/a.ts"]),
                ("pregroomer", [".work/groom/x.md", "C:/p/x/.work/groom/x.md"], ["docs/specs/x.md", "src/a.py"]),
                ("librarian", ["docs/ARCHITECTURE.md"], ["src/a.ts", "tools/work/roles.py"])):
            self.assertEqual({decide(role, "edit", p) for p in mine}, {"allow"}, role)
            self.assertEqual({decide(role, "edit", p) for p in theirs}, {"deny"}, role)

    def test_only_builder_and_integrator_change_the_roles(self):
        self.assertEqual(set(REG["protect"]["may_edit"]), {"builder", "integrator"})
        protected = [".opencode/agents/builder.md", "C:/p/x-wt/y/.opencode/commands/reviewer.md",
                     "tools/work/roles.json", "C:/p/x-wt/y/tools/work/roles.json"]
        for role in FILES:
            want = "allow" if role in REG["protect"]["may_edit"] else "deny"
            self.assertEqual({decide(role, "edit", p) for p in protected}, {want}, role)
            for cmd in ("python -I -X utf8 tools/work/roles.py set builder mode=on", "python tools/work/roles.py gen"):
                self.assertEqual(shell(role, cmd), {want}, (role, cmd))
        if "reviewer" in FILES:  # it fixes minors itself (GREEN WITH NOTES), but never its own rules
            self.assertEqual(decide("reviewer", "edit", "C:/p/x-wt/y/src/a.ts"), "allow")
            self.assertEqual(decide("reviewer", "edit", "C:/p/x-wt/y/.opencode/agents/reviewer.md"), "deny")


def week_db(path, usd):
    name = role_data.main_work(ROOT).parent.name  # what `projects.folder` takes as this project by default
    db = sqlite3.connect(path)
    db.executescript("create table session_v2 (id text, agent text, title text, directory text, time_created integer,"
                     " time_updated integer, model text, cost real);"
                     "create table session_message (id text, session_id text, type text, seq integer, data text);")
    now = int(time.time() * 1000)
    db.execute("insert into session_v2 values ('s1', 'builder', 'X', ?, ?, ?, '{\"providerID\":\"anthropic\"}', ?)",
               (f"C:/p/{name}-wt/x", now, now, usd))
    db.execute("insert into session_v2 values ('s2', 'builder', 'Y', 'D:/elsewhere', ?, ?,"
               " '{\"providerID\":\"anthropic\"}', 999)", (now, now))  # another project's: never counted
    db.commit()
    db.close()
    return path


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.tree = self.tmp / "t"
        for rel in DRIVEN:
            src, dst = TREE / rel, self.tree / rel
            if src.is_dir():
                shutil.copytree(src, dst)
            elif src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
        self.reg = str(self.tree / "tools/work/roles.json")
        self.no_db = ["--db", str(self.tmp / "none.db")]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def roles(self, *args):
        return run("--registry", self.reg, *args)

    def test_set_regenerates_and_may_answers(self):
        self.assertEqual(self.roles("set", "architect", "model=anthropic/claude-opus-5-5", "effort=max")[0], 0)
        for kind in ("agents", "commands"):
            text = (self.tree / ".opencode" / kind / "architect.md").read_text(encoding="utf-8")
            self.assertIn("model: anthropic/claude-opus-5-5#max", text, kind)
        self.assertEqual(self.roles("check")[0], 0)
        self.assertEqual(self.roles("set", "helper", "mode=off")[0], 0)
        rc, out = self.roles(*self.no_db, "may", "helper")
        self.assertTrue(rc == 1 and "helper is off" in out, out)
        self.roles("set", "helper", "mode=on-request")
        self.assertEqual(self.roles(*self.no_db, "may", "helper")[0], 1)
        rc, out = self.roles(*self.no_db, "may", "helper", "--asked")
        self.assertTrue(rc == 0 and "budget was not checked" in out, out)
        self.assertNotEqual(self.roles("set", "helper", "mode=sometimes")[0], 0)
        self.assertEqual(self.roles(*self.no_db, "may", "nobody")[0], 2)
        rc, out = self.roles("show", "--no-db")
        self.assertTrue(rc == 0 and all(f"| {r} |" in out for r in REG["roles"]), out)

    def test_a_spent_budget_warns_and_blocks_on_word(self):
        spent = ["--db", str(week_db(self.tmp / "w.db", 12.5))]
        self.roles("set", "builder", "mode=on", "budget.week=10")
        self.assertEqual(json.loads(Path(self.reg).read_text(encoding="utf-8"))["budget_mode"], "warn")
        rc, out = self.roles(*spent, "may", "builder")
        self.assertEqual(rc, 0, out)
        self.assertIn("over budget: builder $12.50 of $10/week (API $12.50, list $0.00, 1 runs)", out)
        self.assertIn("over budget: builder $12.50",
                      (self.tree / ".work" / "telemetry" / "budget.log").read_text(encoding="utf-8"))
        self.assertEqual(self.roles("budget", "block")[0], 0)
        rc, out = self.roles(*spent, "may", "builder")
        self.assertTrue(rc == 1 and "ask the owner" in out, out)
        self.roles("set", "builder", "budget.week=100")
        self.assertEqual(self.roles(*spent, "may", "builder")[0], 0)
        rc, out = self.roles(*spent, "show")
        self.assertIn("| 1 | 12 | 0 |", out)  # s2 (another project's, $999) is not counted

    def test_check_finds_a_hand_edit(self):
        f = self.tree / ".opencode" / "agents" / "architect.md"
        line = f"model: {roles.model_line(REG['roles']['architect'])}"
        f.write_text(f.read_text(encoding="utf-8").replace(line, line + "x"), encoding="utf-8")
        (self.tree / ".opencode" / "agents" / "stray.md").write_text("---\n---\n", encoding="utf-8")
        rc, out = self.roles("check")
        self.assertEqual(rc, 1, out)
        self.assertIn("drift: .opencode/agents/architect.md", out)
        self.assertIn("stray.md (not in the registry)", out)


if __name__ == "__main__":
    unittest.main()
