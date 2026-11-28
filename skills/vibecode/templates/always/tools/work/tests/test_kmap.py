"""kmap.py over a tiny project built here (specs, decisions, an area doc, modules, TS and Python code, a merge that
moved a spec to done/): `python -I -X utf8 -m unittest discover -s tools/work/tests` (needs git)."""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "kmap.py"
spec = importlib.util.spec_from_file_location("kmap", TOOL)
kmap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kmap)

SPEC = """# {title} (`{id}`)

Status: ready to build
Depends on: {deps}
Same files as: none
Tech: {tech}
{links}
## What it is for

"{title}", the owner.

## What must be true

{rules}

## How to build

{build}
"""
FILES = {
    "docs/specs/minimap-shift.md": SPEC.format(
        id="minimap-shift", title="The minimap shifts its level ahead of the zoom", tech="light",
        deps="`minimap-levels`. `view-later` applies it to each half",
        links="Links: applied-by `view-later` \u00b7 part-of `mod:minimap` \u00b7 not `mod:islands` (only overlay.css)\n",
        rules="1. **The level shifts after a 1.4x turn.** Per decision 2.\n"
              "2. **At most one draw per frame.** In `src/model/minimap.ts` and `src/ui/Minimap.tsx`; styles in "
              "`src/ui/overlay.css`.",
        build="Reuse `mapContainer` in `minimap.ts`."),
    "docs/specs/view-later.md": SPEC.format(
        id="view-later", title="Split view shows both minimaps", tech="heavy", deps="`minimap-shift`", links="",
        rules="1. **Each half shows its own minimap.** `src/ui/split.ts`, `src/ui/overlay.css`.", build="-"),
    "docs/specs/island-drag.md": SPEC.format(
        id="island-drag", title="Drag an island", tech="light", deps="nothing that is open",
        links="Links: part-of `mod:islands`\n",
        rules="1. **An island follows the pointer.** `src/ui/islands.ts`, `src/ui/overlay.css`.", build="-"),
    "docs/specs/minimap-levels.md": SPEC.format(
        id="minimap-levels", title="The minimap picks a level", tech="light", deps="nothing that is open", links="",
        rules="1. **The level comes from the view.** `src/model/levels.ts`.", build="-"),
    **{f"docs/specs/theme-{c}.md": SPEC.format(id=f"theme-{c}", title=f"Theme {c}", tech="light", deps="nothing",
                                               links="", rules="1. **Colours.** `src/ui/overlay.css`.", build="-")
       for c in "abc"},
    "docs/BACKLOG.md": "# Backlog\n\n| # | Batch | What | Status | Waits on | Same files as | Spec |\n"
                       "|---|---|---|---|---|---|---|\n"
                       "| 1 | `minimap-shift` The minimap shifts | x | ready to build | nothing | none | |\n"
                       "| 2 | `view-later` Split minimaps | x | ready to build | `minimap-shift` | none | |\n"
                       "| 3 | `island-drag` Drag | x | ready to build | nothing | none | |\n"
                       "| 4 | `export-csv` Export as CSV | x | needs grooming | nothing | none | |\n",
    "docs/DECISIONS.md": "# Decisions\n\n| # | Decision | Why | Where |\n|---|---|---|---|\n"
                         "| 1 | Levels come from one constant table. | one place | `src/model/levels.ts` |\n"
                         "| 2 | The minimap shifts after 1.4x. Not before. | feel | `minimap-shift` |\n"
                         "| 3 | Levels may be four; amends 1. | depth | `src/model/levels.ts` `src/gone.ts` |\n",
    "docs/ARCHITECTURE.md": "# Architecture\n\n## Layout\n\n| Path | What lives there |\n|---|---|\n"
                            "| `src/ui/` | the UI pieces |\n",
    "docs/architecture/model.md": "# Model: the pure rules\n\n| File | Holds |\n|---|---|\n"
                                  "| `src/model/minimap.ts` | the minimap's level rule |\n"
                                  "| `src/model/levels.ts` | the level table |\n",
    "docs/kmap/modules.md": "# Modules\n\n| Module | What | Seeds |\n|---|---|---|\n"
                            "| `minimap` | the board's minimap | `minimap-shift` `minimap-levels` `src/model/minimap*` |\n"
                            "| `islands` | floating islands | `island-drag` `src/ui/islands.ts` |\n",
    "docs/kmap/golden.md": "# Golden\n\n- `minimap-shift` has `view-later` `src/model/minimap.ts`; not `island-drag`\n",
    "src/model/minimap.ts": "import { LEVELS } from './levels';\nexport function mapContainer(view: number): number {\n"
                            "  return LEVELS[0] + view;\n}\n",
    "src/model/levels.ts": "export const LEVELS = [1, 2, 3];\n",
    "src/model/minimap.test.ts": "import { mapContainer } from './minimap';\n",
    "src/ui/Minimap.tsx": "import { mapContainer } from '../model/minimap';\nexport function Minimap() {\n"
                          "  return mapContainer(1);\n}\n",
    "src/ui/split.ts": "import { Minimap } from './Minimap';\nexport const halves = 2;\n",
    "src/ui/islands.ts": "import './overlay.css';\nexport function drag() {}\n",
    "src/ui/overlay.css": ".a { color: red; }\n",
    "py/app/__init__.py": "",
    "py/app/graph.py": "from .util import helper\nimport app.util\n\n\ndef walk():\n    return helper()\n",
    "py/app/util.py": "def helper():\n    return 1\n",
    "py/tests/test_graph.py": "from app.graph import walk\n",
}


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout


def write(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")


class Kmap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "proj"
        cls.root.mkdir()
        write(cls.root, FILES)
        git(cls.root, "init", "-q", "-b", "main")
        git(cls.root, "add", "-A")
        git(cls.root, "commit", "-qm", "one")
        for i in range(3):  # three small commits that change the same two files: they co-change
            write(cls.root, {"src/ui/Minimap.tsx": FILES["src/ui/Minimap.tsx"] + f"// {i}\n",
                             "src/model/minimap.ts": FILES["src/model/minimap.ts"] + f"// {i}\n"})
            git(cls.root, "commit", "-qam", f"tune {i}")
        git(cls.root, "checkout", "-qb", "levels")
        (cls.root / "docs/specs/done").mkdir()
        git(cls.root, "mv", "docs/specs/minimap-levels.md", "docs/specs/done/minimap-levels.md")
        write(cls.root, {"src/model/levels.ts": "export const LEVELS = [1, 2, 3, 4];\n"})
        git(cls.root, "commit", "-qam", "levels: four")
        git(cls.root, "checkout", "-q", "main")
        git(cls.root, "merge", "-q", "--no-ff", "levels", "-m", "merge levels")
        b = kmap.Builder(cls.root)
        b.build()
        cls.b = b

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_tool(self, *args, env=None, root=None):
        e = {k: v for k, v in os.environ.items() if k != "OPENCODE_SESSION_ID"}
        e.update(env or {})
        done = subprocess.run([sys.executable, "-I", "-X", "utf8", str(TOOL), "--root", str(root or self.root), *args],
                              capture_output=True, text=True, encoding="utf-8", env=e)
        return done.returncode, done.stdout + done.stderr

    def edge(self, src, etype, dst):
        return self.b.edges.get((src, etype, dst))

    def test_build_reads_every_source(self):
        e = self.edge
        self.assertEqual(e("spec:minimap-shift", "depends-on", "spec:minimap-levels")[1], "CURATED")
        self.assertIsNone(e("spec:minimap-shift", "depends-on", "spec:view-later"))  # prose after the period
        self.assertEqual(e("spec:minimap-shift", "applied-by", "spec:view-later")[1], "CURATED")
        self.assertTrue(e("spec:view-later", "depends-on", "spec:minimap-shift"))      # BACKLOG's Waits on too
        self.assertTrue(e("spec:minimap-shift", "touches-file", "file:src/model/minimap.ts"))
        self.assertTrue(e("spec:minimap-shift", "touches-file", "file:src/model/minimap.ts"))
        self.assertTrue(e("spec:minimap-shift", "cites", "dec:2"))
        self.assertTrue(e("dec:1", "binds", "file:src/model/levels.ts"))
        self.assertTrue(e("dec:2", "binds", "spec:minimap-shift"))
        self.assertTrue(e("dec:3", "amends", "dec:1"))
        self.assertTrue(e("area:model", "maps", "file:src/model/minimap.ts"))
        self.assertTrue(e("area:src/ui", "maps", "file:src/ui/split.ts"))
        self.assertTrue(e("file:src/model/minimap.ts", "part-of", "mod:minimap"))      # a glob seed
        self.assertTrue(e("spec:island-drag", "part-of", "mod:islands"))
        self.assertTrue(e("file:src/ui/Minimap.tsx", "imports", "file:src/model/minimap.ts"))
        self.assertTrue(e("file:src/ui/islands.ts", "imports", "file:src/ui/overlay.css"))
        self.assertTrue(e("file:src/model/minimap.ts", "tested-by", "file:src/model/minimap.test.ts"))
        self.assertTrue(e("file:py/app/graph.py", "imports", "file:py/app/util.py"))   # relative and absolute
        self.assertTrue(e("file:py/app/graph.py", "tested-by", "file:py/tests/test_graph.py"))
        self.assertEqual(e("file:src/model/minimap.ts", "co-changes", "file:src/ui/Minimap.tsx")[1], "STAT")
        self.assertTrue(e("spec:minimap-levels", "implemented-in", "file:src/model/levels.ts"))  # the merge
        n = self.b.nodes
        self.assertEqual(n["spec:minimap-levels"]["status"], "done")
        self.assertEqual(n["spec:export-csv"]["status"], "needs grooming")                  # a row with no spec yet
        self.assertEqual(n["file:src/model/minimap.ts"]["l0"], "the minimap's level rule")
        self.assertEqual(n["file:src/model/minimap.test.ts"]["kind"], "test")
        self.assertEqual(n["spec:minimap-shift"]["extra"]["rules"][0], "The level shifts after a 1.4x turn.")
        self.assertIn(("warn", "binds a missing file: docs/DECISIONS.md:7 `src/gone.ts`"), self.b.issues)
        self.assertFalse([t for level, t in self.b.issues if level == "red"])

    def test_query_ranks_and_blocks_and_says_what_is_left(self):
        rc, out = self.run_tool("minimap-shift")
        self.assertEqual(rc, 0, out)
        first, *rows, foot = out.strip().splitlines()
        self.assertTrue(first.startswith("spec minimap-shift \u00b7 The minimap shifts"), first)
        self.assertIn("not mod:islands", first)
        self.assertTrue(any("spec view-later" in r for r in rows[:4]), out)
        self.assertTrue(any("file src/model/minimap.ts" in r for r in rows), out)
        self.assertFalse(any("island-drag" in r or "islands.ts" in r for r in rows), out)  # `not mod:islands`
        self.assertRegex(rows[0], r"^\d\.\d\d ")
        self.assertTrue(foot.startswith("-- ") and "shown of" in foot and "next:" in foot, foot)
        rc, out = self.run_tool("island-drag")   # no `not` there: the hub file still links it, damped
        self.assertEqual(rc, 0, out)
        self.assertIn("file src/ui/islands.ts", out)

    def test_levels(self):
        _, l1 = self.run_tool("minimap-shift", "--level", "1")
        self.assertIn("rule 1: The level shifts after a 1.4x turn.", l1)
        self.assertIn("mapContainer", l1)                                   # a file's declarations
        _, l2 = self.run_tool("src/model/minimap.ts", "--level", "2")
        self.assertRegex(l2, r"src/model/minimap.ts:2 function mapContainer")
        _, l3 = self.run_tool("minimap-shift", "--level", "3")
        self.assertRegex(l3, r"read docs/specs/minimap-shift.md:\d+-\d+ \(What must be true\)")
        self.assertIn("read src/model/minimap.ts", l3)
        self.assertFalse(re.search(r"^\d\.\d\d ", l3, re.M))               # level 3 is a reading list only
        _, why = self.run_tool("view-later", "--why", "--depth", "3")
        self.assertIn("why: spec view-later -", why)

    def test_budget_cuts_and_counts(self):
        _, out = self.run_tool("minimap-shift", "--budget", "4")
        lines = out.strip().splitlines()
        self.assertLessEqual(len(lines), 4)
        self.assertRegex(lines[-1], r"-- \d+ shown of \d+ reached .* \d+ cut by the budget")

    def test_topics_ambiguous_unknown_and_words(self):
        rc, out = self.run_tool("theme")
        self.assertEqual(rc, 3, out)
        self.assertIn("spec:theme-a", out)
        rc, out = self.run_tool("zzzz")
        self.assertEqual(rc, 4, out)
        rc, out = self.run_tool("split minimaps")                         # words: one match is no guess
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith("spec view-later"), out)
        rc, out = self.run_tool("dec:3")
        self.assertEqual(rc, 0, out)
        self.assertIn("dec 1", out)
        rc, out = self.run_tool("minimap-shift", "--levle", "1")
        self.assertEqual(rc, 2, out)                                      # an unknown flag is refused

    def test_path_impact_near(self):
        rc, out = self.run_tool("path", "minimap-shift", "view-later")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"^\d\.\d+ spec minimap-shift -\S+-> spec view-later")
        self.assertNotIn("weak", out)
        _, out = self.run_tool("path", "island-drag", "view-later")      # only a shared stylesheet between them
        self.assertIn("file src/ui/overlay.css", out)
        self.assertIn("weak (under 0.05)", out)
        rc, out = self.run_tool("impact", "src/model/minimap.ts")
        self.assertEqual(rc, 0, out)
        for needle in ("imported-by (1): file src/ui/Minimap.tsx", "tested-by (1): test src/model/minimap.test.ts",
                       "touched-by", "spec minimap-shift"):
            self.assertIn(needle, out)
        rc, out = self.run_tool("near", "island")
        self.assertEqual(rc, 0, out)
        self.assertIn("spec island-drag", out)

    def test_check_green_then_red(self):
        rc, out = self.run_tool("check")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"kmap check: 0 red, \d+ warn")
        self.assertIn("WARN no module: spec view-later", out)
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            write(root, {**FILES, "docs/specs/bad.md": SPEC.format(
                id="bad", title="Bad", tech="light", deps="`ghost` (with `View` in `docker-compose.yml`)",
                links="Links: friends `view-later`\n",
                rules="1. **x.**", build="-"),
                "docs/kmap/golden.md": "- `minimap-shift` not `view-later`\n",
                "docs/kmap/modules.md": FILES["docs/kmap/modules.md"] + "| `empty` | none | `nothing/*` |\n"})
            rc, out = self.run_tool("check", root=root)                   # no git here: still a map
            self.assertEqual(rc, 1, out)
            for needle in ("RED  dangling: docs/specs/bad.md:4 depends-on `ghost` names nothing",
                           "RED  bad link: docs/specs/bad.md:7 `friends `view-later``",
                           "RED  module seed matches nothing: docs/kmap/modules.md:7 `nothing/*`",
                           "RED  golden: docs/kmap/golden.md:1 `minimap-shift` reaches `view-later`"):
                self.assertIn(needle, out)
            self.assertNotIn("`View`", out)                                # prose in a free-text field is no link
            self.assertNotIn("docker-compose", out)

    def test_golden_from_the_fixture_passes(self):
        m = kmap.open_map(self.root)
        self.assertEqual(kmap.golden(self.root, m), [])

    def test_cache_is_reused_until_a_source_changes(self):
        self.run_tool("build")
        dbs = sorted((self.root / ".work/kmap").glob("*.db"))
        self.run_tool("minimap-shift")
        self.assertEqual(sorted((self.root / ".work/kmap").glob("*.db")), dbs)
        p = self.root / "docs/specs/view-later.md"
        old = p.read_text(encoding="utf-8")
        try:
            p.write_text(old + "\nMore `src/ui/islands.ts`.\n", encoding="utf-8", newline="\n")
            _, out = self.run_tool("view-later", "--types", "touches-file", "--depth", "1")
            self.assertIn("file src/ui/islands.ts", out)
            self.assertEqual(len(list((self.root / ".work/kmap").glob("*.db"))), len(dbs) + 1)
        finally:
            p.write_text(old, encoding="utf-8", newline="\n")

    def test_session_marks_seen_and_loops(self):
        env = {"OPENCODE_SESSION_ID": "ses_test1"}
        _, first = self.run_tool("minimap-shift", env=env)
        _, again = self.run_tool("minimap-shift", env=env)
        self.assertNotIn("(seen)", first)
        self.assertIn("(seen) spec view-later", again)
        _, deeper = self.run_tool("minimap-shift", "--level", "1", env=env)
        self.assertNotIn("(seen) spec view-later", deeper)                # a deeper level shows it again
        _, third = self.run_tool("minimap-shift", env=env)
        self.assertTrue(third.startswith("LOOP: spec:minimap-shift asked 3 times at level 0"), third)
        seen = json.loads((self.root / ".work/kmap/seen/ses_test1.json").read_text(encoding="utf-8"))
        self.assertIn("src/model/minimap.ts", seen["paths"])
        self.assertIn("docs/specs/minimap-shift.md", seen["paths"])

    def test_miss_logs_a_vault_line(self):
        rc, out = self.run_tool("miss", 'island-drag needs the "split" seam', "src/ui/split.ts", "--role", "builder")
        self.assertEqual(rc, 0, out)
        line = (self.root / ".work/log/kmap.md").read_text(encoding="utf-8").splitlines()[-1]
        self.assertRegex(line, r"^\d{4}-\d\d-\d\d \d\d:\d\d MISS need=\"island-drag needs the 'split' seam\" "
                               r"found=src/ui/split.ts role=builder$")

    def test_export_writes_notes_and_keeps_edits(self):
        with tempfile.TemporaryDirectory() as vault:
            rc, out = self.run_tool("export", "--vault", vault)
            self.assertEqual(rc, 0, out)
            note = Path(vault, "mesh/spec/minimap-shift.md")
            text = note.read_text(encoding="utf-8")
            self.assertIn("[[mesh/spec/view-later|view-later]]", text)
            self.assertIn("[[mesh/mod/minimap|minimap]] (curated)", text)
            self.assertIn("`src/model/minimap.ts`", text)
            self.assertFalse(Path(vault, "mesh/file").exists())             # files are listed, never notes
            note.write_text(text + "\nmy own note\n", encoding="utf-8")
            rc, out = self.run_tool("export", "--vault", vault)
            self.assertIn("1 kept", out)
            self.assertIn("my own note", note.read_text(encoding="utf-8"))
            self.assertIn("[[mesh/spec/minimap-shift|minimap-shift]]", Path(vault, "mesh/index.md").read_text("utf-8"))

    def test_links_grammar(self):
        items, bad = kmap.parse_links("depends-on `a` `b` \u00b7 not `mod:x` (only a hub) ; shares-ui `mod:y` \u00b7 "
                                      "related `z` \u00b7 none")
        self.assertEqual(items, [("depends-on", "a", ""), ("depends-on", "b", ""), ("not", "mod:x", "only a hub"),
                                 ("shares-ui", "mod:y", "")])
        self.assertEqual(bad, ["related `z`"])


if __name__ == "__main__":
    unittest.main()
