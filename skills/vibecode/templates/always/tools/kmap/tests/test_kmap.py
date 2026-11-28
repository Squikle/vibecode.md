"""`tools/kmap/kmap.py`: the knowledge map over a tiny repository built here (specs, one with two ids in its H1,
BACKLOG with `Waits for` and `Spec`, DECISIONS with a `→`, a MERGES entry, a QUESTIONS row, the code map with a
base folder, modules with a `spec:` glob, TS and Python code, a merge that moved a spec to `done/`). Every run writes
its cache under the test's own folder (`--cache`), never the checkout's.

python -I -X utf8 -m unittest discover -s tools/kmap/tests      (stdlib only; needs git on PATH)"""
import importlib
import json
import os
import re
import subprocess
import sys

import kit

TOOL = kit.KMAP
kmap_build = importlib.import_module("kmap_build")
kmap_cmds = importlib.import_module("kmap_cmds")
kmap_parse = importlib.import_module("kmap_parse")
kmap_store = importlib.import_module("kmap_store")

SPEC = """# {title}

Status: ready to build
Depends on: {deps}
Tech: {tech}
{links}**Try:** {title}

## What it is for

"{title}", the owner.

## What must be true

{rules}

## How to build

{build}
"""
FILES = {
    "docs/specs/minimap-shift.md": SPEC.format(
        title="The minimap shifts its level ahead of the zoom (`minimap-shift`)", tech="light.",
        deps="`minimap-levels`. `view-later` applies it to each half",
        links="Links: applied-by `view-later` \u00b7 part-of `mod:minimap` \u00b7 not `mod:islands` (only overlay.css)\n",
        rules="1. **The level shifts after a 1.4x turn.** Per decision 2.\n"
              "2. **At most one draw per frame.** In `src/model/minimap.ts` and `src/ui/Minimap.tsx`; styles "
              "in `src/ui/overlay.css`.",
        build="Reuse `mapContainer` in `minimap.ts`."),
    "docs/specs/view-later.md": SPEC.format(
        title="Split view shows both minimaps (`view-later`)", tech="heavy", deps="`minimap-shift` (built).", links="",
        rules="1. **Each half shows its own minimap.** `src/ui/split.ts`, `src/ui/overlay.css`.", build="-"),
    "docs/specs/island-drag.md": SPEC.format(
        title="Drag an island (`island-drag`)", tech="light", deps="nothing that is open",
        links="Links: part-of `mod:islands`\n",
        rules="1. **An island follows the pointer.** `src/ui/islands.ts`, `src/ui/overlay.css`.", build="-"),
    "docs/specs/minimap-levels.md": SPEC.format(
        title="The minimap picks a level (`minimap-levels`)", tech="light", deps="nothing that is open", links="",
        rules="1. **The level comes from the view.** `src/model/levels.ts`.", build="-"),
    "docs/specs/link-routes.md": SPEC.format(
        title="Links as routed cables (`link-routes-a`, `link-routes-b`)", tech="heavy", deps="nothing", links="",
        rules="1. **One bus.** Decisions 3 to 5 and D001.", build="-"),
    "docs/specs/scope-a.md": SPEC.format(
        title="A scope (`scope-a`)", tech="light", deps="`theme-b`", links="",
        rules="1. **Like `theme-c`, but its own.**", build="-"),
    **{f"docs/specs/theme-{c}.md": SPEC.format(title=f"Theme {c} (`theme-{c}`)", tech="light", deps="nothing",
                                               links="", rules="1. **Colours.** `src/ui/overlay.css`.", build="-")
       for c in "abc"},
    "docs/BACKLOG.md": "# Backlog\n\n| # | Batch | Area | Status | Waits for | Spec |\n|---|---|---|---|---|---|\n"
                       "| 7 | `minimap-shift` The minimap shifts | Board | ready | nothing | `docs/specs/minimap-shift.md` |\n"
                       "| 3 | `view-later` Split `L` and `R` minimaps | Board | ready | `minimap-shift` | |\n"
                       "| 9 | `island-drag` Drag | Panels | ready | nothing | |\n"
                       "| 4 | `export-csv` Export as CSV | Later | needs grooming | nothing | |\n"
                       "| 5 | `link-routes-a` Cut A | Links | ready | nothing | `docs/specs/link-routes.md` |\n",
    "docs/DECISIONS.md": "# Decisions\n\n| # | Decision | Binds |\n|---|---|---|\n"
                         "| 1 | Levels come from one constant table. | `src/model/levels.ts` |\n"
                         "| 2 | The minimap shifts after 1.4x. Not before. | `minimap-shift` |\n"
                         "| 3 | Levels may be four; amends 1. | `src/model/levels.ts` `src/gone.ts` |\n"
                         "| 4 | \u2192 5. A route is one path. | `link-routes-a` |\n"
                         "| 5 | A route is one path between two ports. | `link-routes-a` |\n",
    "docs/QUESTIONS.md": "# Questions\n\n## Small questions and the choices taken\n\n"
                         "- **Halves** (view-later, 2026-11-24): `view-later` keeps `src/ui/split.ts` as is.\n"
                         "  A second line of the same row.\n- A row that names nothing.\n",
    "docs/ARCHITECTURE.md": "# Code map\n\n## 2. Board (`src/`)\n\n| Layer | Folder | What belongs there |\n"
                            "|---|---|---|\n| 4 islands | `ui/` | the UI pieces |\n",
    "docs/architecture/model.md": "# Code map: `model/` (the rules)\n\n| File | Holds |\n|---|---|\n"
                                  "| `minimap.ts` | the minimap's level rule |\n| `levels.ts` | the level table |\n",
    "docs/kmap/modules.md": "# Modules\n\n| Module | What | Seeds |\n|---|---|---|\n"
                            "| `minimap` | the board's minimap | `spec:minimap-*` `src/model/minimap*` |\n"
                            "| `islands` | floating islands | `island-drag` `src/ui/islands.ts` |\n",
    "docs/kmap/golden.md": "# Golden\n\n- `minimap-shift` has `view-later` `src/model/minimap.ts`; not `island-drag`\n"
                           "- `minimap-shift` via `needed-by` `view-later`\n"
                           "- `minimap-levels` has `src/model/levels.ts` (git)\n",
    "src/model/minimap.ts": "import { LEVELS } from './levels';\nexport function mapContainer(view: number): number {\n"
                                "  return LEVELS[0] + view;\n}\n\nexport function unrelatedPaint(): number {\n"
                                + "  const a = 1;\n" * 60 + "  return a;\n}\n",
    "src/model/levels.ts": "export const LEVELS = [1, 2, 3];\n",
    "src/scene/levels.ts": "export const DRAWN = 1;\n",
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


def build_repo(top):
    r = kit.Repo(top / "proj")
    for rel, text in FILES.items():
        r.write(rel, text.replace("\r\n", "\n"))
    r.commit("one")
    for i in range(3):  # three small commits that change the same two files: they co-change
        r.write("src/ui/Minimap.tsx", FILES["src/ui/Minimap.tsx"] + f"// {i}\n")
        r.write("src/model/minimap.ts", FILES["src/model/minimap.ts"] + f"// {i}\n")
        r.commit(f"tune {i}")
    r.branch("levels")
    (r.path / "docs/specs/done").mkdir()
    r.git("mv", "docs/specs/minimap-levels.md", "docs/specs/done/minimap-levels.md")
    r.write("src/model/levels.ts", "export const LEVELS = [1, 2, 3, 4];\n")
    r.commit("levels: four")
    r.git("checkout", "-q", "main")
    r.git("merge", "-q", "--no-ff", "levels", "-m", "merge levels")
    r.branch("routes")
    r.write("src/ui/islands.ts", FILES["src/ui/islands.ts"] + "export const routed = 1;\n")
    r.commit("routes: a")
    r.git("checkout", "-q", "main")
    r.git("merge", "-q", "--no-ff", "routes", "-m", "merge routes")
    sha = r.git("rev-parse", "--short=8", "HEAD")
    r.write("docs/MERGES.md", f"# Merges\n\n### 2026-11-27 10:00 \u00b7 link-routes-a \u00b7 {sha}\n- **What:** cut A\n")
    r.commit("merges: link-routes-a")
    b = kmap_build.Builder(r.path)
    b.build()
    return r.path, b


def run(root, *args, cache=None, env=None):
    e = {k: v for k, v in os.environ.items() if k not in ("OPENCODE_SESSION_ID", "KMAP_CACHE")}
    e.update(env or {})
    done = subprocess.run([sys.executable, "-I", "-X", "utf8", str(TOOL), "--root", str(root),
                           "--cache", str(cache or root.parent / "cache"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=e, timeout=120)
    return done.returncode, done.stdout + done.stderr


def test_build_reads_every_source(repo):
    _, b = repo
    e = lambda s, t, d: b.edges.get((s, t, d))  # noqa: E731
    assert e("spec:minimap-shift", "depends-on", "spec:minimap-levels")[1] == "CURATED"
    assert e("spec:minimap-shift", "depends-on", "spec:view-later") is None            # prose after the period
    assert e("spec:minimap-shift", "applied-by", "spec:view-later")[1] == "CURATED"
    assert e("spec:view-later", "depends-on", "spec:minimap-shift")                      # BACKLOG's Waits for too
    for edge in [("spec:minimap-shift", "touches-file", "file:src/model/minimap.ts"),
                 ("spec:minimap-shift", "cites", "dec:2"), ("spec:link-routes", "cites", "dec:4"),  # "3 to 5"
                 ("spec:link-routes", "cites", "dec:1"),                                 # D001
                 ("dec:1", "binds", "file:src/model/levels.ts"), ("dec:2", "binds", "spec:minimap-shift"),
                 ("dec:3", "amends", "dec:1"), ("dec:5", "supersedes", "dec:4"),        # `→ 5`
                 ("area:model", "maps", "file:src/model/levels.ts"),                # the H1's `model/` first
                 ("area:src/ui", "maps", "file:src/ui/split.ts"),               # a heading's `src/`
                 ("file:src/model/minimap.ts", "part-of", "mod:minimap"),
                 ("spec:minimap-levels", "part-of", "mod:minimap"),                     # a `spec:` glob seed
                 ("spec:island-drag", "part-of", "mod:islands"),
                 ("spec:link-routes-a", "specified-in", "spec:link-routes"),            # the H1 and the Spec column
                 ("q:5", "asks-about", "spec:view-later"), ("q:5", "asks-about", "file:src/ui/split.ts"),
                 ("file:src/ui/Minimap.tsx", "imports", "file:src/model/minimap.ts"),
                 ("file:src/ui/islands.ts", "imports", "file:src/ui/overlay.css"),
                 ("file:src/model/minimap.ts", "tested-by", "file:src/model/minimap.test.ts"),
                 ("file:py/app/graph.py", "imports", "file:py/app/util.py"),
                 ("file:py/app/graph.py", "tested-by", "file:py/tests/test_graph.py"),
                 ("spec:minimap-levels", "implemented-in", "file:src/model/levels.ts"),  # the merge to done/
                 ("spec:link-routes-a", "implemented-in", "file:src/ui/islands.ts")]:    # the MERGES entry
        assert e(*edge), edge
    assert e("file:src/model/minimap.ts", "co-changes", "file:src/ui/Minimap.tsx")[1] == "STAT"
    assert ("area:model", "maps", "file:src/scene/levels.ts") not in b.edges
    n = b.nodes
    assert (n["spec:minimap-levels"]["status"], n["spec:export-csv"]["status"]) == ("done", "needs grooming")
    assert n["spec:link-routes-a"]["status"] == "ready"                 # the row wins over the merge entry
    assert n["spec:view-later"]["l0"] == "Split view shows both minimaps"
    assert n["file:src/model/minimap.ts"]["l0"] == "the minimap's level rule"
    assert n["file:src/model/minimap.test.ts"]["kind"] == "test"
    assert n["spec:minimap-shift"]["extra"]["rules"][0] == "The level shifts after a 1.4x turn."
    assert "q:7" not in n                                               # a row that names nothing is no node
    assert "spec:link-routes-b" not in n                                # an H1 id with no row and no merge either
    assert ("warn", "binds a missing file: docs/DECISIONS.md:7 `src/gone.ts`") in b.issues
    assert [t for level, t in b.issues if level == "red"] == []


def test_query_ranks_and_blocks_and_says_what_is_left(repo):
    root, _ = repo
    rc, out = run(root, "minimap-shift")
    assert rc == 0, out
    first, *rows, foot = out.strip().splitlines()
    assert first.startswith("spec minimap-shift \u00b7 The minimap shifts") and "not mod:islands" in first
    assert any("spec view-later" in r for r in rows[:4]), out
    assert any("file src/model/minimap.ts" in r for r in rows), out
    assert not any("island-drag" in r or "islands.ts" in r for r in rows), out  # `not mod:islands`
    assert re.match(r"^\d\.\d\d ", rows[0]) and foot.startswith("-- ") and "shown of" in foot and "next:" in foot
    rc, out = run(root, "island-drag")   # no `not` there: the hub file still links it, damped
    assert rc == 0 and "file src/ui/islands.ts" in out, out


def test_levels_budget_and_why(repo):
    root, _ = repo
    _, l1 = run(root, "minimap-shift", "--level", "1")
    assert "rule 1: The level shifts after a 1.4x turn." in l1 and "mapContainer" in l1
    assert "try: The minimap shifts its level ahead of the zoom" in l1
    _, l2 = run(root, "src/model/minimap.ts", "--level", "2")
    assert re.search(r"src/model/minimap.ts:2 function mapContainer", l2), l2
    _, l3 = run(root, "minimap-shift", "--level", "3")
    assert re.search(r"read docs/specs/minimap-shift.md:\d+-\d+ \(What must be true\)", l3), l3
    assert not re.search(r"^\d\.\d\d ", l3, re.M)
    # the spec's backticked `mapContainer` picks its lines in the working tree; the unrelated function is left out
    assert re.search(r"^read src/model/minimap.ts:2-4 mapContainer \(\d+ lines\)$", l3, re.M), l3
    _, why = run(root, "view-later", "--why", "--depth", "3")
    assert "why: spec view-later -" in why
    _, out = run(root, "minimap-shift", "--budget", "4")
    lines = out.strip().splitlines()
    assert len(lines) <= 4 and re.search(r"-- \d+ shown of \d+ reached .* \d+ cut by the budget", lines[-1])


@kit.cases(
    (["theme"], 3, "spec:theme-a"),                      # ambiguous: candidates, never a guess
    (["zzzz"], 4, "not found: zzzz"),
    (["split minimaps"], 0, "spec view-later"),          # words: one match
    (["dec:3"], 0, "dec 1"),
    (["minimap-shift", "--levle", "1"], 2, "unrecognized"),
)
def test_topics(repo, args, code, needle):
    rc, out = run(repo[0], *args)
    assert rc == code and needle in out, out


def test_path_impact_near(repo):
    root, _ = repo
    rc, out = run(root, "path", "minimap-shift", "view-later")
    assert rc == 0 and re.match(r"^\d\.\d+ spec minimap-shift -\S+-> spec view-later", out) and "weak" not in out, out
    _, out = run(root, "path", "island-drag", "theme-a")   # only a shared stylesheet between them
    assert "file src/ui/overlay.css" in out and "weak (under 0.05)" in out, out
    rc, out = run(root, "impact", "src/model/minimap.ts")
    assert rc == 0, out
    for needle in ("imported-by (1): file src/ui/Minimap.tsx", "tested-by (1): test src/model/minimap.test.ts",
                   "touched-by", "spec minimap-shift"):
        assert needle in out, out
    rc, out = run(root, "near", "island")
    assert rc == 0 and "spec island-drag" in out, out


def test_check_green_with_and_without_git_then_red(repo, tmp_path):
    root, _ = repo
    rc, out = run(root, "check")
    assert rc == 0 and re.search(r"kmap check: 0 red, \d+ warn .* git yes\)", out), out
    assert "WARN no module: spec view-later" in out
    rc, out = run(root, "--no-git", "check")   # the gate's export: no git links, the `(git)` golden line skipped
    assert rc == 0 and "git no)" in out, out
    bad = tmp_path / "bad"
    for rel, text in {**FILES, "docs/specs/bad.md": SPEC.format(
            title="Bad (`bad`)", tech="light", deps="`ghost-spec`, `main` at `7afad4b9` (with `View` in `docker-compose.yml`)",
            links="Links: friends `view-later`\n", rules="1. **x.**", build="-"),
            "docs/kmap/golden.md": "- `minimap-shift` not `view-later`\n- `minimap-shift` via `mentions` `view-later`\n",
            "docs/kmap/modules.md": FILES["docs/kmap/modules.md"] + "| `empty` | none | `nothing/*` `spec:zz-*` |\n"}.items():
        (bad / rel).parent.mkdir(parents=True, exist_ok=True)
        (bad / rel).write_text(text, encoding="utf-8", newline="\n")
    rc, out = run(bad, "check")                                        # no git here: still a map
    assert rc == 1, out
    for needle in ("RED  dangling: docs/specs/bad.md:4 depends-on `ghost-spec` names nothing",
                   "RED  bad link: docs/specs/bad.md:6 `friends `view-later``",
                   "RED  module seed matches nothing: docs/kmap/modules.md:7 `nothing/*`",
                   "RED  module seed matches nothing: docs/kmap/modules.md:7 `spec:zz-*`",
                   "RED  golden: docs/kmap/golden.md:1 `minimap-shift` reaches `view-later`",
                   "RED  golden: docs/kmap/golden.md:2 `minimap-shift` reaches `view-later` via `needed-by`, not `mentions`"):
        assert needle in out, out
    assert "`View`" not in out and "docker-compose" not in out      # prose in a free-text field is no link
    assert "`main`" not in out and "7afad4b9" not in out            # nor a lone word or a sha that names nothing


def test_a_mention_ranks_below_a_real_depends_on(repo, tmp_path):
    """N1: `scope-a` depends on `theme-b` and only mentions `theme-c` in its body: the dependency leads, by its link."""
    m = kmap_store.open_map(repo[0], cache=tmp_path)
    ranked, via, _ = importlib.import_module("kmap_query").flow(m, ["spec:scope-a"], 2)
    score = dict(ranked)
    assert score["spec:theme-b"] > 2 * score["spec:theme-c"], (score["spec:theme-b"], score["spec:theme-c"])
    assert (via["spec:theme-b"][1], via["spec:theme-c"][1]) == ("depends-on", "mentions")


def test_golden_of_the_fixture_passes(repo, tmp_path):
    root, _ = repo
    assert kmap_cmds.golden(root, kmap_store.open_map(root, cache=tmp_path)) == []


def test_cache_is_reused_until_a_source_changes_and_stays_where_it_is_told(repo, tmp_path):
    root, _ = repo
    cache = tmp_path / "c"
    run(root, "build", cache=cache)
    dbs = sorted(cache.glob("*.db"))
    run(root, "minimap-shift", cache=cache)
    assert sorted(cache.glob("*.db")) == dbs and len(dbs) == 1
    assert not (root / ".work").exists()                               # nothing in the checkout's own .work
    p = root / "docs/specs/view-later.md"
    old = p.read_text(encoding="utf-8")
    try:
        p.write_text(old + "\nMore `src/ui/islands.ts`.\n", encoding="utf-8", newline="\n")
        _, out = run(root, "view-later", "--types", "touches-file", "--depth", "1", cache=cache)
        assert "file src/ui/islands.ts" in out and len(list(cache.glob("*.db"))) == 2
    finally:
        p.write_text(old, encoding="utf-8", newline="\n")


def test_session_marks_seen_and_loops(repo, tmp_path):
    root, _ = repo
    cache, env = tmp_path / "s", {"OPENCODE_SESSION_ID": "sess-test1"}
    _, first = run(root, "minimap-shift", cache=cache, env=env)
    _, again = run(root, "minimap-shift", cache=cache, env=env)
    assert "(seen)" not in first and "(seen) spec view-later" in again
    _, deeper = run(root, "minimap-shift", "--level", "1", cache=cache, env=env)
    assert "(seen) spec view-later" not in deeper                    # a deeper level shows it again
    _, third = run(root, "minimap-shift", cache=cache, env=env)
    assert third.startswith("LOOP: spec:minimap-shift asked 3 times at level 0"), third
    seen = json.loads((cache / "seen/sess-test1.json").read_text(encoding="utf-8"))
    assert {"src/model/minimap.ts", "docs/specs/minimap-shift.md"} <= set(seen["paths"])


def test_miss_is_a_self_report_candidate(repo, tmp_path):
    root, _ = repo
    cache = tmp_path / "m"
    rc, out = run(root, "miss", "island-drag", "src/ui/split.ts", "--why", 'needs the "split" seam',
                  cache=cache, env={"OPENCODE_SESSION_ID": "sess-m1"})
    assert rc == 0 and "weak evidence" in out, out
    line = json.loads((cache / "candidates.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert {k: line[k] for k in ("a", "b", "kind", "evidence", "why")} == {
        "a": "spec:island-drag", "b": "file:src/ui/split.ts", "kind": "self-report", "evidence": "sess-m1",
        "why": 'needs the "split" seam'} and re.match(r"\d{4}-\d\d-\d\dT", line["at"])
    assert not (root / ".work/log/kmap.md").exists()
    rc, out = run(root, "miss", "theme", "src/ui/split.ts", cache=cache)        # never a guess between two
    assert rc == 3 and "spec:theme-a" in out, out


def test_export_regenerates_every_note_and_deletes_the_gone(repo, tmp_path):
    """The vault export always regenerates; edit the source in git, not the note."""
    root, _ = repo
    vault = tmp_path / "vault"
    gone = vault / "mesh/spec/ghost.md"
    gone.parent.mkdir(parents=True)
    gone.write_text("an old node's note\n", encoding="utf-8")
    (vault / "mesh/.kmap-export.json").write_text("{}", encoding="utf-8")            # phase 1's hashes: dropped
    rc, out = run(root, "export", "--vault", str(vault))
    assert rc == 0 and re.search(r"export: \d+ notes written, 0 unchanged, 1 deleted at [0-9a-f]{8} ", out), out
    assert not gone.exists() and not (vault / "mesh/.kmap-export.json").exists()
    note = vault / "mesh/spec/minimap-shift.md"
    text = note.read_text(encoding="utf-8")
    sha = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=8", "HEAD"], capture_output=True,
                         text=True).stdout.strip()
    assert text.split("---\n")[2].startswith(f"> generated by kmap at `{sha}`; edit the source "
                                             "(`docs/specs/minimap-shift.md`), not this note\n"), text
    assert "[[mesh/spec/view-later|view-later]]" in text and "[[mesh/mod/minimap|minimap]] (curated)" in text
    assert "`src/model/minimap.ts`" in text and not (vault / "mesh/file").exists()   # files listed, never notes
    index = vault / "mesh/index.md"
    assert index.read_text(encoding="utf-8").startswith(f"> generated by kmap at `{sha}`")
    note.write_text(text + "\nmy own note\n", encoding="utf-8")
    rc, out = run(root, "export", "--vault", str(vault))
    assert re.search(r"export: 1 notes written, \d+ unchanged, 0 deleted", out), out       # the edit is overwritten
    assert note.read_text(encoding="utf-8") == text


def test_list_is_the_inventory_and_an_area_question_hints_at_it(tmp_path):
    """`kmap.py list`: every member of an area, unranked and uncut, by sub-folder; `kmap.py <area>` whose relations
    all sit under the floor (an area of 30 files that import each other) starts with the inventory hint."""
    panel = {f"src/panel/{d}/p{d}{i:02}.ts": f"import './p{d}{i + 1:02}';\nexport function show{d.upper()}{i}() {{}}\n"
             for d in "ab" for i in range(1, 16)}
    panel["src/panel/a/pa01.test.ts"] = "import { showA1 } from './pa01';\n"
    rows = "".join(f"| `{p[len('src/panel/'):]}` | panel {p.rsplit('/', 1)[1][1:-3]} |\n" for p in panel)
    files = {**FILES, **panel, "docs/architecture/panel.md": "# Code map: `panel/` (the panels)\n\n| File | Holds |\n"
             "|---|---|\n" + rows}
    root = tmp_path / "inv"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
    rc, out = run(root, "panel", cache=tmp_path / "c")
    assert rc == 0 and out.startswith("hint: this looks like an inventory question: kmap.py list panel --group"), out
    rc, out = run(root, "list", "panel", "--group", cache=tmp_path / "c")
    lines = out.splitlines()
    assert rc == 0 and lines[0] == "list area panel: 31 members: file 30, test 1, under src/panel/", out
    assert lines[1] == "a/ (16)" and lines[2] == "  pa01.test.ts \u00b7 panel a01.test" and "b/ (15)" in lines, out
    assert "  pb15.ts \u00b7 panel b15" in lines and len(lines) == 1 + 2 + 31, out           # every member, uncut
    _, out = run(root, "list", "panel", "--level", "0", cache=tmp_path / "c")
    assert "src/panel/b/pb07.ts" in out.splitlines() and "\u00b7" not in out, out
    _, out = run(root, "list", "src/panel/b", "--kind", "symbol", cache=tmp_path / "c")
    assert out.splitlines()[0].startswith("list folder src/panel/b: 15 symbols") and \
        "src/panel/b/pb03.ts:2-2 showB3" in out.splitlines(), out
    _, out = run(root, "minimap-shift", cache=tmp_path / "c")
    assert "hint:" not in out                                          # a spec: what relates to it
    rc, out = run(root, "list", "minimap-shift", cache=tmp_path / "c")
    assert rc == 2 and "list takes an area, a module or a folder" in out, out


@kit.cases(
    ("a.ts", "/** Doc. */\nexport function f(a: string) {\n  const s = `x ${a} }`;\n  // }\n  return '{';\n}\n"
             "export const LIST = [\n  1,\n];\nexport type T =\n  | 'a'\n  | 'b';\n"
             "export class Box {\n  private n = 0;\n  async grow(by: number): Promise<void> {\n    if (by) {\n"
             "      this.n += by;\n    }\n  }\n  get size() { return this.n; }\n}\nconst tail = 1\n",
     [("f", 1, 6), ("LIST", 7, 9), ("T", 10, 12), ("Box", 13, 21), ("Box.grow", 15, 19), ("Box.size", 20, 20),
      ("tail", 22, 22)]),
    ("a.py", "import os\n\n\n@cache\ndef f():\n    return 1\n\n\nclass K:\n    def m(self):\n        pass\n",
     [("f", 4, 6), ("K", 9, 11), ("K.m", 10, 11)]),
)
def test_level3_symbols_and_their_lines(path, text, want):
    lines = importlib.import_module("kmap_lines")
    assert lines.symbols(path, text) == want
    assert lines.score("mapContainer", {"mapContainer"}, set()) == 3 and lines.score("shiftLevel", set(), {"level"}) == 1


def test_links_grammar():
    items, bad = kmap_parse.parse_links("depends-on `a` `b` \u00b7 not `mod:x` (only a hub) ; shares-ui `mod:y` \u00b7 "
                                        "related `z` \u00b7 none")
    assert items == [("depends-on", "a", ""), ("depends-on", "b", ""), ("not", "mod:x", "only a hub"),
                     ("shares-ui", "mod:y", "")]
    assert bad == ["related `z`"]


Kmap = kit.suite(globals(), repo=build_repo)
