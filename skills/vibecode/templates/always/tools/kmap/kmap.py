#!/usr/bin/env python3
"""The knowledge map: what relates to a spec, a file, a decision or a module, ranked and cut to a budget, with no model.

`python -I -X utf8 tools/kmap/kmap.py <topic> [--level 0-3] [--depth 1-3] [--budget N] [--role R] [--types t,..]
                                     [--why] [--json] [--session ID]`
  <topic>: an id (`spec:x`, `file:src/a.ts`, `dec:12`, `mod:x`, `area:x`, `feat:3`), a bare name (a batch id, a
  path or its tail, a module, a decision number) or words (matched against names and one-liners). It prints the topic,
  then what relates to it, ranked by a flow from it: each hop decays, splits over the node's links and stops under a
  threshold; a hub file, decision or question row is damped by its degree; a `not` link in the topic's `Links:` line
  blocks that module. One node per line `score kind id · one-liner · via how`; marks: `!` curated (Links, Depends on,
  Waits for, modules), `*` statistics (co-change). A footer says what was left out and what to do next. Levels: 0 one
  line each; 1 adds the facts (a spec's Try and rules, a file's declarations, a decision's text, a module's members);
  2 sections and outlines with line numbers; 3 only what to read (`read path:a-b`; a code file by its best symbols'
  line ranges in the working tree now, `read src/app/view.ts:120-188 render`). `--role product|architect|
  plan-review|builder|reviewer|librarian|designer` weighs the link types that role needs. With `--session` (default
  $OPENCODE_SESSION_ID) a node shown before at this level or deeper prints as `(seen)`, the third ask of one topic at
  one level prints a LOOP line, and the paths it returned are kept (`<cache>/seen/<session>.json`) for
  `tools/work/role-telemetry.py`'s off-map count.
  An area, module or folder whose best relation is under 0.05 starts with a `hint:` line naming `list`.
`kmap.py list <area|module|folder> [--kind file|spec|test|symbol] [--group] [--level 0|1]`: "what is in X": every
  member, unranked and never cut, by sub-folder or module with `--group`, one-liners at level 1, names only past 150.
`kmap.py near "<words>"`: the closest names. `kmap.py path <a> <b>`: how two things connect, or that they don't.
`kmap.py impact <topic>`: what links to it (importers, tests, specs, decisions, modules).
`kmap.py miss <topic> <what I found> [--why "<my words>"]`: a link the map lacked, as a `self-report` candidate
  (weak: it becomes a link only with a mechanical source behind the same pair, or the architect's yes).
`kmap.py check`: red on a dangling id or a bad type in a curated link, a module seed that matches nothing, a failed
  golden query (`docs/kmap/golden.md`); hubs, binds to missing files and open specs in no module are warnings. Exit 1
  on red.
`kmap.py recall <sha> [--batch id] [--k 20] [--log]`: the map at the merge's first parent (built from that tree, no
  checkout) against the code files the merge changed: one `RECALL batch= sha= k= recall= precision= new= missed=` line
  and a `MERGES:` line (`merge-log.py` adds it to the entry); `--log` appends `<cache>/recall.jsonl` and the merge's
  candidates. `--replay N`: the last N merges with a spec, each at its parent, and the baseline's means and medians.
`kmap.py candidates [--top N] [--review] [--accept <ids>] [--reject <ids>]`: the miss candidates of `<cache>/candidates.jsonl`
  by pair and score; 3 independent mechanical evidences, or a self-report and one mechanical, make a `found` link
  (`kmap_found.py`); `--review` the top 20 waiting by a stable pair id (git-style shortest unique prefix), `--accept`/`--reject [--reason]` write them
  into `docs/kmap/reviewed.md` (`kmap_review.py`); a rejection is a snapshot of both ends, back in review when one
  changes, new evidence comes, or after 20 merges that changed either end (30 days at most). No model makes or types a link. One detection is one
  evidence (co-change: 3 distinct merges); a spec ~ file recall miss never promotes, its merge links the pair at once.
`kmap.py build [--force]` · `kmap.py stats [--json]` · `kmap.py export --vault <dir> [--rev <sha>]` (Obsidian notes in
  <dir>/mesh/, every one regenerated, each saying the sha it was made at; a note whose node is gone is deleted).
Every command takes `--root <dir>` (default: this checkout), `--cache <dir>` (default `$KMAP_CACHE`, else the main
checkout's `.work/kmap/`) and `--no-git` (build from the files alone, as the gate's export does).

Sources, read from the worktree as it is: specs (`docs/specs/**`: the `Depends on:`, `Same files as:` and `Links:`
header lines, the ids in the H1, backticked paths and ids, "decision 12"), `docs/BACKLOG.md` rows (status, `Waits for`,
the `Spec` column), `docs/DECISIONS.md` rows (the last column binds files and specs; "amends 12"; `→ 12`),
`docs/MERGES.md` entries (merged ids; their commit's files), `docs/QUESTIONS.md` rows
that name a spec or a file, the tables of `docs/ARCHITECTURE.md` and `docs/architecture/*.md` (an area maps its files),
`docs/kmap/modules.md` (declared modules and their seeds: ids, paths, globs, `spec:` globs), code (TS/JS relative
imports, Python imports by `ast`, tests by name and import), and git's first-parent history. Exit 0 an answer, 2 bad
usage, 3 an ambiguous topic (candidates listed, never a guess), 4 not found (near names listed), 1 a red check.
Standard library only; writes only the cache (with `recall.jsonl`, `candidates.jsonl`, `review.json`),
`docs/kmap/reviewed.md` on `candidates --accept/--reject`, and the vault folder you name.
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))              # `python -I` leaves the script's folder off sys.path
from kmap_cmds import cmd_check, cmd_export, cmd_impact, cmd_stats  # noqa: E402
from kmap_found import self_report  # noqa: E402
from kmap_list import KINDS as KIND_CHOICES, cmd_list  # noqa: E402
from kmap_parse import RECALL_K, REVIEW_MAX, ROLES, run_git  # noqa: E402
from kmap_query import cmd_path, describe, near, query  # noqa: E402
from kmap_recall import cmd_recall  # noqa: E402
from kmap_review import cmd_candidates  # noqa: E402
from kmap_store import CANDIDATES, cache_dir, open_map, open_map_at  # noqa: E402

SUBCOMMANDS = {"near", "path", "impact", "miss", "check", "build", "stats", "export", "recall", "candidates", "list"}


def parser(sub):
    ap = argparse.ArgumentParser(prog="kmap.py" + (f" {sub}" if sub else ""), allow_abbrev=False,
                                 description=__doc__.split("\n\n")[0])
    if sub is None:
        ap.add_argument("topic")
        ap.add_argument("--level", type=int, choices=range(4), default=0)
        ap.add_argument("--depth", type=int, choices=range(1, 4), default=2)
        ap.add_argument("--budget", type=int, default=0, help="lines (default 40; 120 at level 2)")
        ap.add_argument("--role", choices=sorted(ROLES))
        ap.add_argument("--types", default="", help="follow only these link types, comma-separated")
        ap.add_argument("--why", action="store_true")
        ap.add_argument("--json", action="store_true")
        ap.add_argument("--session", default=os.environ.get("OPENCODE_SESSION_ID", ""))
    elif sub == "near":
        ap.add_argument("words")
    elif sub == "path":
        ap.add_argument("a")
        ap.add_argument("b")
    elif sub == "impact":
        ap.add_argument("topic")
        ap.add_argument("--budget", type=int, default=12, help="names per line")
    elif sub == "miss":
        ap.add_argument("topic", help="what you asked about: an id, a path or words that name one node")
        ap.add_argument("found", help="what you needed and found elsewhere: a path or an id")
        ap.add_argument("--why", default="", help="in your words, why it belongs (kept with the candidate)")
        ap.add_argument("--session", default=os.environ.get("OPENCODE_SESSION_ID", ""))
    elif sub == "build":
        ap.add_argument("--force", action="store_true")
    elif sub == "stats":
        ap.add_argument("--json", action="store_true")
    elif sub == "export":
        ap.add_argument("--vault", required=True)
        ap.add_argument("--rev", help="export the map of this commit (built from its tree), not the working tree's")
    elif sub == "recall":
        ap.add_argument("sha", nargs="?", default="HEAD")
        ap.add_argument("--batch", help="the batch id (default: the sha); its spec is the topic")
        ap.add_argument("--k", type=int, default=RECALL_K)
        ap.add_argument("--log", action="store_true", help="append recall.jsonl and the candidates")
        ap.add_argument("--replay", type=int, default=0, metavar="N", help="the baseline over the last N merges")
    elif sub == "list":
        ap.add_argument("topic", help="an area, a module or a folder")
        ap.add_argument("--kind", choices=KIND_CHOICES)
        ap.add_argument("--group", action="store_true", help="by sub-folder (files) or module (specs)")
        ap.add_argument("--level", type=int, choices=range(2), default=1, help="1: with one-liners; 0: names only")
    elif sub == "candidates":
        ap.add_argument("--top", type=int, default=REVIEW_MAX)
        ap.add_argument("--review", action="store_true", help="the top waiting pairs as an accept/reject table, by stable pair id")
        ap.add_argument("--accept", default="", help="pair ids from --review, comma-separated")
        ap.add_argument("--reject", default="", help="pair ids from --review, comma-separated")
        ap.add_argument("--reason", default="", help="why: quoted when the pair comes back")
        ap.add_argument("--reviewer", default="architect")
    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    common = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    common.add_argument("--root", default=str(HERE.parents[1]), help="the project (default: this checkout)")
    common.add_argument("--cache", default=None)
    common.add_argument("--no-git", action="store_true")
    known, rest = common.parse_known_args(argv)
    root = Path(known.root).resolve()
    sub = rest[0] if rest and rest[0] in SUBCOMMANDS else None
    a = parser(sub).parse_args(rest[1:] if sub else rest)
    if sub == "recall":
        return cmd_recall(root, a, known.cache)
    if sub == "export" and a.rev:
        m = open_map_at(root, a.rev, known.cache, found=True)
        return cmd_export(m, a, m.meta["sha"])
    m = open_map(root, force=sub == "build" and a.force, cache=known.cache, git=not known.no_git)
    if sub == "build":
        print(f"kmap: {len(m.nodes)} nodes, {len(m.edges)} links, built in {m.meta.get('seconds')} s")
        return 0
    if sub == "near":
        hits = near(m, a.words)
        print("\n".join(describe(m, h) for h in hits) or "nothing near it")
        return 0 if hits else 4
    if sub is None:
        sid = re.sub(r"[^\w-]", "", a.session or "")
        return query(m, a, cache_dir(root, known.cache) / "seen" / f"{sid}.json" if sid else None)
    head = ((run_git(root, "rev-parse", "HEAD") if not known.no_git else None) or "worktree").strip()
    folder = cache_dir(root, known.cache)
    return {"path": lambda: cmd_path(m, a), "impact": lambda: cmd_impact(m, a), "check": lambda: cmd_check(root, m, a),
            "stats": lambda: cmd_stats(m, a), "export": lambda: cmd_export(m, a, head),
            "miss": lambda: self_report(m, a, folder / CANDIDATES, time.time()), "list": lambda: cmd_list(m, a, root),
            "candidates": lambda: cmd_candidates(root, m, a, folder / CANDIDATES, folder,
                                                  lambda: open_map(root, cache=known.cache, git=not known.no_git))}[sub]()


if __name__ == "__main__":
    sys.exit(main())
