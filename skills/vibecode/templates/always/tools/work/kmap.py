#!/usr/bin/env python3
"""The knowledge map: what relates to a spec, a file, a decision or a module, ranked and cut to a budget, with no model.

`python -I -X utf8 tools/work/kmap.py <topic> [--level 0-3] [--depth 1-3] [--budget N] [--role R] [--types t,..]
                                     [--why] [--json] [--session ID]`
  <topic>: an id (`spec:x`, `file:src/a.ts`, `dec:12`, `mod:x`, `area:x`), a bare name (a spec id, a path, a module,
  a decision number) or words (matched against names and one-liners). It prints the topic, then what relates to it,
  ranked by a flow from it: each hop decays, splits over the node's links and stops under a threshold; a hub file or
  decision is damped by its degree; a `not` link in the topic's `Links:` line blocks that module. One node per line
  `score kind id · one-liner · via how`; marks: `!` curated (Links, Depends on, modules), `*` statistics (co-change).
  A footer says what was left out and what to do next. Levels: 0 one line each; 1 adds the facts (a spec's status,
  tech and rules, a file's declarations, a decision's text); 2 sections and outlines with line numbers; 3 only what
  to read (`read path:a-b`). `--role product|architect|plan-review|builder|reviewer|librarian` weighs the link types
  that role needs. With `--session` (default: $OPENCODE_SESSION_ID) a node shown before at this level or deeper
  prints as `(seen)`, the third ask of one topic at one level prints a LOOP line, and the paths it returned are kept
  (`.work/kmap/seen/<session>.json`) for role-telemetry.py's off-map count.
`kmap.py near "<words>"`: the closest names. `kmap.py path <a> <b>`: how two things connect, or that they don't.
`kmap.py impact <topic>`: what links to it (importers, tests, specs, decisions, modules).
`kmap.py miss "<what I needed>" <where I found it>`: logs a link the map lacked (`.work/log/kmap.md`, vault shape).
`kmap.py check`: red on a dangling id or a bad type in a curated link, a module seed that matches nothing, a failed
  golden query (`docs/kmap/golden.md`); hubs, binds to missing files and specs in no module are warnings. Exit 1 on red.
`kmap.py build [--force]` · `kmap.py stats [--json]` · `kmap.py export --vault <dir>` (Obsidian notes in <dir>/mesh/;
  a note edited there since the last export is kept).

Sources, read from the worktree as it is: specs (`docs/specs/**`: the `Depends on:`, `Same files as:` and `Links:`
header lines, backticked paths and ids, "decision 12"), `docs/BACKLOG.md` rows (status, `Waits on`, `Same files as`),
`docs/DECISIONS.md` rows (the last column binds files and specs; "amends 12"), the table rows of
`docs/ARCHITECTURE.md` and `docs/architecture/*.md` (an area maps its files), `docs/kmap/modules.md` (declared modules
and their seeds: spec ids, paths, globs), code (TS/JS relative imports, Python imports by `ast`, tests by name and
import), and git: the first-parent commit that moved a spec into `docs/specs/done/` implemented it in the files it
changed; files changed together in 3+ small commits co-change. The map is a SQLite file per HEAD and worktree state in
the main checkout's `.work/kmap/` (8 kept), rebuilt in about a second when missing.
Exit 0 an answer, 2 bad usage, 3 an ambiguous topic (candidates listed, never a guess), 4 not found (near names
listed), 1 a red check. Stdlib only; writes only under `.work/` (and the vault folder you name).
"""
import argparse
import ast
import collections
import fnmatch
import hashlib
import importlib.util
import json
import math
import os
import posixpath
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = 1
KEEP_DBS = 8
ALPHA, THETA = 0.7, 0.01        # decay per hop; a share under THETA stops
WEAK = 0.05                     # `path`: a chain scored under this is called weak
HISTORY = 400                   # first-parent commits read for implemented-in and co-change
CO_MAX, CO_SUPPORT, CO_CONF = 25, 3, 0.5
HUB_MIN = 30                    # a degree over max(HUB_MIN, p99) is a hub warning
MAX_BYTES = 1_000_000           # a bigger file is a node, never parsed
BUDGET = {0: 40, 1: 40, 2: 120, 3: 40}
CODE = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".css", ".scss", ".html", ".vue", ".svelte", ".go",
        ".rs", ".java", ".kt", ".cs", ".rb", ".php", ".sh", ".ps1", ".sql"}
SKIPPED = {"node_modules", "dist", "build", "__pycache__", ".venv", "venv", ".git", ".work", "e2e-results",
           "test-results", "coverage"}
LINK_TYPES = {"depends-on", "applied-by", "applies", "shares-ui", "supersedes", "conflicts", "part-of", "not"}
KINDS = ("mod", "spec", "dec", "area", "file")    # id prefixes; a test file is `file:` of kind `test`
# base weight and trust per edge type; CURATED links are written by a role, EXTRACTED parsed, STAT counted
EDGE = {"depends-on": 1.0, "applied-by": 0.9, "applies": 0.9, "supersedes": 1.0, "conflicts": 0.8, "shares-ui": 0.5,
        "part-of": 1.0, "same-files-as": 0.6, "implemented-in": 0.9, "touches-file": 0.8, "binds": 0.8, "cites": 0.7,
        "amends": 1.0, "maps": 0.6, "imports": 0.7, "tested-by": 0.7, "mentions": 0.4, "co-changes": 0.5}
REVERSE = {"depends-on": "needed-by", "applied-by": "applies", "applies": "applied-by", "supersedes": "superseded-by",
           "part-of": "has", "implemented-in": "implements", "touches-file": "touched-by", "binds": "bound-by",
           "cites": "cited-by", "amends": "amended-by", "maps": "mapped-in", "imports": "imported-by",
           "tested-by": "tests", "mentions": "mentioned-by"}
BACK = {"implemented-in": 0.6, "touches-file": 0.6, "mentions": 0.6, "imports": 0.8}   # walking an edge backwards
ROLES = {
    "product": {"depends-on": 1.5, "same-files-as": 1.2, "cites": 1.5, "binds": 1.2, "mentions": 1.3, "part-of": 1.3,
                "conflicts": 2.0, "implemented-in": 0.6, "imports": 0.3, "co-changes": 0.5},
    "architect": {"imports": 1.5, "co-changes": 1.5, "maps": 1.3, "binds": 1.3, "implemented-in": 1.2,
                  "depends-on": 1.2},
    "plan-review": {"depends-on": 1.5, "cites": 1.5, "binds": 1.3, "conflicts": 2.0, "mentions": 1.2, "maps": 1.2},
    "builder": {"implemented-in": 1.5, "touches-file": 1.5, "tested-by": 1.5, "imports": 1.2, "binds": 1.2},
    "reviewer": {"binds": 1.5, "tested-by": 1.5, "imports": 1.2, "cites": 1.2},
    "librarian": {"mentions": 1.5, "cites": 1.5, "binds": 1.3, "supersedes": 1.3, "amends": 1.3},
}
MARK = {"CURATED": "!", "STAT": "*"}
TICK = re.compile(r"`([^`\n]{1,160})`")
H1 = re.compile(r"^#\s+(.+?)\s*$")
HEADING = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
HEADER = re.compile(r"^(Status|Depends on|Same files as|Tech|Links|Review):\s*(.*?)\s*$")
RULE = re.compile(r"^\s*\d+\.\s+\*\*(.+?)\*\*")
CITE = re.compile(r"\bdecisions?\s+#?(\d+(?:\s*(?:,|and|or|&)\s*#?\d+)*)", re.I)
AMENDS = re.compile(r"\b(?:amends|replaces|supersedes)\s+(?:decision\s+)?#?(\d+)", re.I)
TS_IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)['"]([^'"\n]+)['"]""", re.M)
SEP = re.compile(r":?-{2,}:?")
ID_SHAPE = re.compile(r"(?:(?:spec|mod|dec|area):)?[a-z0-9][a-z0-9-]*")


def run_git(root, *args):
    try:
        r = subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=off", *args], capture_output=True)
    except OSError:
        return None
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def load_map_py():
    """map.py beside this file gives the outlines; without it a file has no outline."""
    spec = importlib.util.spec_from_file_location("vibecode_map", HERE / "map.py")
    if not spec or not (HERE / "map.py").is_file():
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:  # an outline is a nicety; never fail the map for it
        return None
    return mod


def is_test(path):
    name = path.rsplit("/", 1)[-1]
    return ".test." in name or ".spec." in name or name.startswith("test_") or name.endswith("_test.py") \
        or name == "conftest.py"


def table_rows(lines):
    """(line number, header cells lower-cased, cells) for each body row of each markdown table."""
    header = None
    for number, line in enumerate(lines, 1):
        s = line.strip()
        if not s.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
        elif not all(SEP.fullmatch(c) for c in cells if c):
            yield number, header, cells


def sections(lines):
    """[title, first line, last line] for each `##`+ heading, outside code fences."""
    out, fenced = [], False
    for number, line in enumerate(lines, 1):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and (m := HEADING.match(line)):
            if out:
                out[-1][2] = number - 1
            out.append([m.group(2), number, len(lines)])
    return out


# ---------------------------------------------------------------- build

class Builder:
    def __init__(self, root):
        self.root = root
        self.nodes, self.edges, self.alias, self.issues, self.pending = {}, {}, {}, [], []
        self.files, self.by_base = set(), collections.defaultdict(list)
        self.mapmod = load_map_py()

    def node(self, nid, kind, l0="", path=None, status="", **extra):
        n = self.nodes.setdefault(nid, {"kind": kind, "l0": "", "path": None, "status": "", "extra": {}})
        n["l0"] = n["l0"] or l0[:160]
        n["path"] = n["path"] or path
        n["status"] = status or n["status"]
        n["extra"].update(extra)
        return n

    def edge(self, src, etype, dst, conf="EXTRACTED", evidence="", weight=None):
        if src == dst or src not in self.nodes or dst not in self.nodes:
            return
        key = (src, etype, dst)
        w = EDGE[etype] if weight is None else weight
        old = self.edges.get(key)
        if not old or old[0] < w or (conf == "CURATED" and old[1] != "CURATED"):
            self.edges[key] = (w, conf, evidence)

    def file_of(self, token):
        t = token.strip().split(":")[0].replace("\\", "/")
        t = t[2:] if t.startswith("./") else t
        if t in self.files:
            return t
        if "." not in t.rsplit("/", 1)[-1]:
            return None
        hits = self.by_base.get(t.rsplit("/", 1)[-1], [])
        if "/" in t:
            hits = [h for h in hits if h.endswith("/" + t)]
        return hits[0] if len(hits) == 1 else None

    def ref(self, token):
        """A curated token -> a node id, or None: `mod:x`, `spec:x`, `dec:3`, a bare spec or module id, a path."""
        t = token.strip()
        kind, _, key = t.partition(":")
        if kind in KINDS and key:
            if kind == "file":
                f = self.file_of(key)
                return f"file:{f}" if f else None
            return t if t in self.nodes else self.alias.get(t)
        for cand in (f"spec:{t}", f"mod:{t}"):
            if cand in self.nodes:
                return cand
        f = self.file_of(t)
        if f:
            return f"file:{f}"
        return self.alias.get(f"spec:{t}")

    def build(self):
        t0 = time.time()
        tracked = run_git(self.root, "ls-files", "-co", "--exclude-standard")
        if tracked is None:
            paths = [p.relative_to(self.root).as_posix() for p in walk(self.root)]
        else:
            paths = [p for p in tracked.splitlines() if p and not SKIPPED & set(p.split("/")[:-1])]
        paths = [p for p in paths if (self.root / p).is_file()]
        code = [p for p in paths if posixpath.splitext(p)[1] in CODE and not p.startswith("docs/")]
        for p in code:
            self.files.add(p)
            self.by_base[p.rsplit("/", 1)[-1]].append(p)
            self.node(f"file:{p}", "test" if is_test(p) else "file", path=p)
        specs = sorted(p for p in paths if p.startswith("docs/specs/") and p.endswith(".md")
                       and not p.rsplit("/", 1)[-1].startswith(("_", "README")))
        for p in specs:  # nodes first: a spec's links name other specs
            sid = p.rsplit("/", 1)[-1][:-3]
            self.node(f"spec:{sid}", "spec", path=p, status="done" if "/done/" in p else "")
        self.read_modules(paths)
        self.read_backlog()
        self.read_decisions()
        self.read_areas(paths)
        for p in specs:
            self.read_spec(p)
        self.read_code(code)
        self.read_git()
        self.read_module_seeds()
        for src, etype, token, evidence in self.pending:
            dst = self.ref(token)
            if dst:
                self.edge(src, etype, dst, "CURATED", evidence)
            else:  # a built spec's links rot by design: a warning there, a red in open work
                self.issues.append(("warn" if "/done/" in evidence else "red",
                                    f"dangling: {evidence} {etype} `{token}` names nothing"))
        return time.time() - t0

    def text(self, rel):
        try:
            p = self.root / rel
            return p.read_text(encoding="utf-8-sig", errors="replace") if p.stat().st_size <= MAX_BYTES else ""
        except OSError:
            return ""

    def read_modules(self, paths):
        self.module_rows = []
        lines = self.text("docs/kmap/modules.md").splitlines()
        for number, header, cells in table_rows(lines):
            ids = TICK.findall(cells[0]) if cells else []
            if not ids:
                continue
            mid = "mod:" + ids[0].removeprefix("mod:")
            self.node(mid, "mod", cells[1] if len(cells) > 1 else "", path="docs/kmap/modules.md", line=number)
            self.module_rows.append((mid, TICK.findall(cells[2]) if len(cells) > 2 else [], number))

    def read_module_seeds(self):
        for mid, seeds, number in self.module_rows:
            for seed in seeds:
                where = f"docs/kmap/modules.md:{number}"
                if any(c in seed for c in "*?["):
                    hit = [f for f in self.files if fnmatch.fnmatch(f, seed)]
                    for f in hit:
                        self.edge(f"file:{f}", "part-of", mid, "CURATED", where)
                    if not hit:
                        self.issues.append(("red", f"module seed matches nothing: {where} `{seed}`"))
                    continue
                dst = self.ref(seed)
                if dst and dst != mid:
                    self.edge(dst, "part-of", mid, "CURATED", where)
                else:
                    self.issues.append(("red", f"module seed names nothing: {where} `{seed}`"))

    def read_backlog(self):
        lines = self.text("docs/BACKLOG.md").splitlines()
        for number, header, cells in table_rows(lines):
            if "batch" not in header or "status" not in header:
                continue
            col = {h: i for i, h in enumerate(header)}
            ids = TICK.findall(cells[col["batch"]]) if col["batch"] < len(cells) else []
            if not ids:
                continue
            sid = f"spec:{ids[0]}"
            what = cells[col["what"]] if "what" in col and col["what"] < len(cells) else ""
            title = TICK.sub("", cells[col["batch"]]).strip() or what
            status = cells[col["status"]] if col["status"] < len(cells) else ""
            self.node(sid, "spec", title, status=status, backlog=number)
            for name, etype in (("waits on", "depends-on"), ("same files as", "same-files-as")):
                if name in col and col[name] < len(cells):
                    for tok in ids_in(cells[col[name]]):
                        self.pending.append((sid, etype, tok, f"docs/BACKLOG.md:{number}"))

    def read_decisions(self):
        lines = self.text("docs/DECISIONS.md").splitlines()
        self.decision_binds = []
        for number, header, cells in table_rows(lines):
            if not cells or not cells[0].isdigit():
                continue
            did = f"dec:{cells[0]}"
            text = " | ".join(cells[1:])
            first = re.split(r"(?<=[.!?])\s", cells[1] if len(cells) > 1 else "", maxsplit=1)[0]
            self.node(did, "dec", first, path="docs/DECISIONS.md", line=number, text=text[:600])
            self.decision_binds.append((did, TICK.findall(cells[-1]) if len(cells) > 2 else [], number, text))

    def read_areas(self, paths):
        docs = ["docs/ARCHITECTURE.md"] + sorted(p for p in paths if p.startswith("docs/architecture/")
                                                 and p.endswith(".md"))
        for doc in docs:
            lines = self.text(doc).splitlines()
            own = doc.rsplit("/", 1)[-1][:-3] if doc != "docs/ARCHITECTURE.md" else None
            for number, header, cells in table_rows(lines):
                col = next((i for i, h in enumerate(header) if h in ("path", "file", "where")), 0)
                toks = TICK.findall(cells[col]) if col < len(cells) else []
                job = next((c for i, c in enumerate(cells) if i != col and c), "")
                for tok in toks:
                    tok = tok.rstrip("/")
                    f = self.file_of(tok)
                    under = [x for x in sorted(self.files) if x.startswith(tok + "/")] if not f else []
                    if f:
                        self.nodes[f"file:{f}"]["l0"] = self.nodes[f"file:{f}"]["l0"] or job[:160]
                    # an area doc maps every file and folder row; ARCHITECTURE.md's folder rows are areas of their own
                    aid = f"area:{own}" if own else (f"area:{tok}" if under else None)
                    if not aid or not (f or under):
                        continue
                    title = (lines[0].lstrip("# ").strip() if lines else own) if own else job
                    self.node(aid, "area", title, path=doc, line=number)
                    if f:
                        self.edge(aid, "maps", f"file:{f}", evidence=f"{doc}:{number}")
                    for x in under[:300]:
                        self.edge(aid, "maps", f"file:{x}", evidence=f"{doc}:{number}", weight=0.3)

    def read_spec(self, rel):
        sid = "spec:" + rel.rsplit("/", 1)[-1][:-3]
        lines = self.text(rel).splitlines()
        head, rules, inside = {}, [], False
        for number, line in enumerate(lines, 1):
            if line.startswith("## "):
                break
            if m := HEADER.match(line):
                head[m.group(1)] = (m.group(2), number)
        for line in lines:
            if line.startswith("## "):
                inside = line[3:].strip().lower().startswith("what must be true")
            elif inside and (m := RULE.match(line)):
                rules.append(m.group(1).strip()[:140])
        title = next((H1.match(x).group(1) for x in lines[:3] if H1.match(x)), "")
        n = self.nodes[sid]
        n["l0"] = re.sub(r"\s*\(`[^`]+`\)\s*$", "", title)[:160] or n["l0"]
        # done/ wins; else the backlog row's status (read first); else the spec's own Status line
        n["status"] = ("done" if "/done/" in rel else n["status"] or head.get("Status", ("",))[0])[:60]
        tech = re.match(r"[\w-]*", head.get("Tech", ("",))[0]).group(0)   # `light`, `heavy`: the first word
        n["extra"].update(rules=rules[:12], tech=tech, sections=sections(lines),
                          lines=len(lines))
        for field, etype in (("Depends on", "depends-on"), ("Same files as", "same-files-as")):
            value, number = head.get(field, ("", 0))
            value = re.split(r"\.(?:\s|$)", value, maxsplit=1)[0]   # free text after the first period is prose
            for tok in ids_in(value):
                self.pending.append((sid, etype, tok, f"{rel}:{number}"))
        value, number = head.get("Links", ("", 0))
        items, bad = parse_links(value)
        for part in bad:
            self.issues.append(("red", f"bad link: {rel}:{number} `{part[:60]}` (types: {', '.join(sorted(LINK_TYPES))})"))
        for etype, tok, _ in items:
            if etype == "not":
                dst = self.ref(tok)
                if dst:
                    self.nodes[sid]["extra"].setdefault("not", []).append(dst)
                else:
                    self.issues.append(("red", f"dangling: {rel}:{number} not `{tok}` names nothing"))
            else:
                self.pending.append((sid, etype, tok, f"{rel}:{number}"))
        body_start = next((i for i, x in enumerate(lines) if x.startswith("## ")), len(lines))
        for number, line in enumerate(lines[body_start:], body_start + 1):
            for tok in TICK.findall(line):
                f = self.file_of(tok) if ("/" in tok or "." in tok) else None
                if f:
                    self.edge(sid, "touches-file", f"file:{f}", evidence=f"{rel}:{number}")
                elif f"spec:{tok}" in self.nodes and f"spec:{tok}" != sid:
                    self.edge(sid, "mentions", f"spec:{tok}", evidence=f"{rel}:{number}")
            for m in CITE.finditer(line):
                for d in re.findall(r"\d+", m.group(1)):
                    self.edge(sid, "cites", f"dec:{d}", evidence=f"{rel}:{number}")

    def read_code(self, code):
        py = [p for p in code if p.endswith(".py")]
        dotted = collections.defaultdict(set)
        for p in py:
            parts = p[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for k in range(len(parts)):
                dotted[".".join(parts[k:])].add(p)
        modules = {k: next(iter(v)) for k, v in dotted.items() if len(v) == 1}
        for p in code:
            ext = posixpath.splitext(p)[1]
            if ext not in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py"):
                continue
            text = self.text(p)
            if not text:
                continue
            if ext == ".py":
                outline, targets = self.py_scan(p, text, modules)
            else:
                found = self.mapmod.ts_outline(text) if self.mapmod else []
                outline = [[line, what] for line, depth, what, _ in found if depth == 0][:60]
                targets = self.ts_imports(p, text)
            self.nodes[f"file:{p}"]["extra"].update(lines=text.count("\n") + 1, outline=outline)
            for dst in sorted(set(targets)):
                if dst != p:
                    etype = "tested-by" if is_test(p) and not is_test(dst) else "imports"
                    src_, dst_ = (dst, p) if etype == "tested-by" else (p, dst)
                    self.edge(f"file:{src_}", etype, f"file:{dst_}", evidence=p)
        for p in code:
            if not is_test(p):
                continue
            name = p.rsplit("/", 1)[-1]
            stem = re.sub(r"\.(test|spec)\.", ".", name)
            stem = stem[5:] if stem.startswith("test_") else stem.replace("_test.py", ".py")
            same_dir = posixpath.join(posixpath.dirname(p), stem)
            hits = [same_dir] if same_dir in self.files else [h for h in self.by_base.get(stem, []) if not is_test(h)]
            if len(hits) == 1 and hits[0] != p:
                self.edge(f"file:{hits[0]}", "tested-by", f"file:{p}", evidence="by name")

    def ts_imports(self, src, text):
        out = []
        for spec in TS_IMPORT.findall(text):
            if not spec.startswith("."):
                continue
            base = posixpath.normpath(posixpath.join(posixpath.dirname(src), spec))
            stem = re.sub(r"\.(js|jsx|mjs|cjs)$", "", base)
            for cand in [base] + [stem + e for e in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".d.ts")] + \
                        [base + "/index" + e for e in (".ts", ".tsx", ".js", ".jsx")]:
                if cand in self.files:
                    out.append(cand)
                    break
        return out

    def py_scan(self, src, text, modules):
        """One parse: (top-level outline like map.py's, imported project files). Statements only, never expressions:
        an import sits in a module, class, function, if or try body."""
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return [], []
        outline = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                outline.append([node.lineno, f"{'class' if isinstance(node, ast.ClassDef) else 'def'} {node.name}"])
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                outline += [[node.lineno, f"const {t.id}"] for t in targets if isinstance(t, ast.Name) and t.id.isupper()]

        def rel(path):
            for cand in (path + ".py", path + "/__init__.py"):
                if cand in self.files:
                    return cand
            return None

        def statements(body):
            for node in body:
                yield node
                for field in ("body", "orelse", "finalbody", "handlers"):
                    yield from statements(getattr(node, field, None) or [])

        out = []
        for node in statements(tree.body):
            if isinstance(node, ast.Import):
                out += [modules.get(a.name) for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level:
                base = posixpath.dirname(src)
                for _ in range(node.level - 1):
                    base = posixpath.dirname(base)
                root = posixpath.join(base, *(node.module or "").split(".")) if node.module else base
                out += [rel(root)] + [rel(posixpath.join(root, a.name)) for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                out += [modules.get(node.module)] + [modules.get(f"{node.module}.{a.name}") for a in node.names]
        return outline[:60], [x for x in out if x]

    def read_git(self):
        out = run_git(self.root, "log", "--first-parent", f"-n{HISTORY}", "--diff-merges=first-parent",
                      "--name-status", "-M", "--format=@@%h", "HEAD")
        if out is None:
            out = run_git(self.root, "log", "--first-parent", f"-n{HISTORY}", "-m", "--name-status", "-M",
                          "--format=@@%h", "HEAD") or ""
        commits, cur = [], None
        for line in out.splitlines():
            if line.startswith("@@"):
                cur = {"sha": line[2:].strip(), "done": [], "files": []}
                commits.append(cur)
            elif line.strip() and cur is not None:
                parts = line.split("\t")
                status, path = parts[0], parts[-1]
                if status.startswith("R") and len(parts) == 3 and path in self.files:
                    self.alias[f"file:{parts[1]}"] = f"file:{path}"
                if path.startswith("docs/specs/done/") and path.endswith(".md") and status[:1] in "RA":
                    cur["done"].append("spec:" + path.rsplit("/", 1)[-1][:-3])
                if path in self.files and status[:1] != "D":
                    cur["files"].append(path)
        seen, pairs = collections.Counter(), collections.Counter()
        for c in commits:
            for sid in c["done"]:
                if sid in self.nodes:
                    for f in c["files"]:
                        self.edge(sid, "implemented-in", f"file:{f}", evidence=f"commit {c['sha']}")
            fs = sorted(set(c["files"]))
            if 2 <= len(fs) <= CO_MAX:
                seen.update(fs)
                pairs.update((fs[i], fs[j]) for i in range(len(fs)) for j in range(i + 1, len(fs)))
        for (a, b), n in pairs.items():
            if n >= CO_SUPPORT and n / min(seen[a], seen[b]) >= CO_CONF:
                self.edge(f"file:{a}", "co-changes", f"file:{b}", "STAT", f"{n} commits")
        for did, toks, number, text in self.decision_binds:
            for tok in toks:
                dst = self.ref(tok)
                if dst:
                    self.edge(did, "binds", dst, evidence=f"docs/DECISIONS.md:{number}")
                elif "/" in tok or "." in tok.rsplit("/", 1)[-1]:
                    self.issues.append(("warn", f"binds a missing file: docs/DECISIONS.md:{number} `{tok}`"))
            for m in AMENDS.finditer(text):
                self.edge(did, "amends", f"dec:{m.group(1)}", evidence=f"docs/DECISIONS.md:{number}")


def parse_links(value):
    """`<type> `id` (reason)` items joined by ` · ` or `;` -> ([(type, id, reason)], [bad items])."""
    items, bad = [], []
    for part in re.split(r"\s+\u00b7\s+|\s*;\s*", value.strip()):
        part = part.strip()
        if not part or part.lower() in ("none", "-", "nothing"):
            continue
        m = re.match(r"([a-z][a-z-]*)\s+`", part)
        ids = TICK.findall(part)
        if not m or not ids or m.group(1) not in LINK_TYPES:
            bad.append(part)
            continue
        reason = re.search(r"\(([^)]*)\)\s*$", part)
        items += [(m.group(1), i, reason.group(1) if reason else "") for i in ids]
    return items, bad


def ids_in(text):
    """The id-shaped backticked tokens of a free-text field (`Depends on:`, a row's `Waits on`): a path, a flag or a
    word in capitals there is prose, not a link."""
    return [t for t in TICK.findall(text) if ID_SHAPE.fullmatch(t)]


def walk(root):
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED)
        for name in sorted(files):
            yield Path(folder, name)


# ---------------------------------------------------------------- the cache

def main_tree(root):
    """The main checkout (the folder holding `.git`) of a worktree; the root itself outside git."""
    common = run_git(root, "rev-parse", "--git-common-dir")
    if not common:
        return root
    p = Path(common.strip())
    p = p if p.is_absolute() else (root / p)
    return p.resolve().parent if p.name == ".git" else root


def state_key(root):
    """HEAD plus the size and time of every source the build reads plus this tool's own bytes."""
    h = hashlib.sha1(f"{SCHEMA}".encode())
    h.update(Path(__file__).read_bytes())
    listing = run_git(root, "ls-files", "-co", "--exclude-standard")
    paths = listing.splitlines() if listing is not None else [p.relative_to(root).as_posix() for p in walk(root)]
    for p in sorted(paths):
        if p.startswith("docs/") or posixpath.splitext(p)[1] in CODE:
            try:
                st = (root / p).stat()
            except OSError:
                continue
            h.update(f"{p}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    head = (run_git(root, "rev-parse", "HEAD") or "nogit").strip()[:12]
    return f"{head}-{h.hexdigest()[:10]}"


def write_db(path, b, seconds):
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    db = sqlite3.connect(tmp)
    db.executescript("""
        create table node(id text primary key, kind text, l0 text, path text, status text, extra text);
        create table edge(src text, type text, dst text, weight real, conf text, evidence text,
                          primary key(src, type, dst));
        create table alias(alias text primary key, id text);
        create table issue(level text, text text);
        create table meta(k text primary key, v text);""")
    db.executemany("insert into node values (?,?,?,?,?,?)",
                   [(k, n["kind"], n["l0"], n["path"], n["status"], json.dumps(n["extra"])) for k, n in b.nodes.items()])
    db.executemany("insert into edge values (?,?,?,?,?,?)", [(s, t, d, *v) for (s, t, d), v in b.edges.items()])
    db.executemany("insert into alias values (?,?)", list(b.alias.items()))
    db.executemany("insert into issue values (?,?)", b.issues)
    db.executemany("insert into meta values (?,?)", [("seconds", f"{seconds:.2f}"), ("built", str(int(time.time())))])
    db.commit()
    db.close()
    try:
        os.replace(tmp, path)
    except OSError:  # another lane built the same state and has it open (Windows): theirs is as good
        tmp.unlink(missing_ok=True)
        if not path.is_file():
            raise


def prune(folder):
    dbs = sorted(folder.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in dbs[KEEP_DBS:]:
        try:
            old.unlink()
        except OSError:
            pass  # another process holds it; the next prune gets it


class Map:
    def __init__(self, path):
        db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        self.nodes = {r[0]: {"kind": r[1], "l0": r[2] or "", "path": r[3], "status": r[4] or "",
                             "extra": json.loads(r[5] or "{}")} for r in db.execute("select * from node")}
        self.edges = [r for r in db.execute("select src, type, dst, weight, conf, evidence from edge")]
        self.alias = dict(db.execute("select alias, id from alias"))
        self.issues = list(db.execute("select level, text from issue"))
        self.meta = dict(db.execute("select k, v from meta"))
        db.close()
        self.adj = collections.defaultdict(list)
        for s, t, d, w, conf, ev in self.edges:
            self.adj[s].append((d, t, t, w, conf))
            self.adj[d].append((s, t, REVERSE.get(t, t), w * BACK.get(t, 1.0), conf))
        self.deg = {n: len(v) for n, v in self.adj.items()}


def open_map(root, force=False):
    cache = main_tree(root) / ".work" / "kmap"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{state_key(root)}.db"
    if force or not path.is_file():
        b = Builder(root)
        seconds = b.build()
        write_db(path, b, seconds)
        prune(cache)
    else:
        os.utime(path)  # most recently used
    return Map(path)


# ---------------------------------------------------------------- query

def resolve(m, topic):
    """("ok", [id]) | ("ambiguous", candidates) | ("none", near names). Never a guess between two."""
    t = topic.strip().replace("\\", "/")
    kind, _, key = t.partition(":")
    if kind in KINDS and key:
        for cand in (t, m.alias.get(t)):
            if cand in m.nodes:
                return "ok", [cand]
        return "none", near(m, key)
    exact = [f"{k}:{t}" for k in ("mod", "spec", "dec", "area") if f"{k}:{t}" in m.nodes]
    files = [n for n in m.nodes if n.startswith("file:") and (n[5:] == t or n[5:].endswith("/" + t))]
    if not files and f"file:{t}" in m.alias:
        files = [m.alias[f"file:{t}"]]
    exact += files if len(files) == 1 or not exact else []
    if len(exact) == 1:
        return "ok", exact
    if exact:
        return "ambiguous", exact
    cands = near(m, t)
    return ("ok", cands) if len(cands) == 1 else ("ambiguous" if cands else "none", cands)


def near(m, words, top=10):
    ws = [w for w in re.split(r"[\s/:._-]+", words.lower()) if w]
    scored = []
    for nid, n in m.nodes.items():
        idl, text = nid.lower(), f"{nid} {n['l0']}".lower()
        if ws and all(w in text for w in ws):
            scored.append((-sum(w in idl for w in ws), len(nid), nid))
    return [nid for *_, nid in sorted(scored)[:top]]


def refuse(state, topic, cands):
    if state == "ambiguous":
        print(f"ambiguous: {topic} could be " + " | ".join(cands[:10]) + "; ask again with one of these ids")
        return 3
    print(f"not found: {topic}" + (f"; near: {' | '.join(cands[:10])}" if cands else "; nothing near it"))
    return 4


def blocked_by(m, seeds):
    out = set()
    for s in seeds:
        for target in m.nodes[s]["extra"].get("not", []):
            out.add(target)
            if target.startswith("mod:"):
                out |= {o for o, t, label, *_ in m.adj[target] if label == "has"}
    return out - set(seeds)


def damp(m, node):
    kind = node.split(":", 1)[0]
    d = m.deg.get(node, 1)
    return 1 / (1 + math.log(d)) if kind in ("file", "dec", "area") and d > 1 else 1.0


def flow(m, seeds, depth, role=None, types=None):
    """A PathRAG-style flow from the seeds -> (ranked [(id, score)], via {id: (from, label, conf)}, mass lost)."""
    prof = ROLES.get(role or "", {})
    block = blocked_by(m, seeds)
    score, via, best = {s: 1.0 for s in seeds}, {}, {}
    frontier, reached, lost = list(seeds), set(seeds), 0.0
    for _ in range(depth):
        nxt = []
        for n in frontier:
            nb = m.adj.get(n, [])
            split = score[n] / math.sqrt(len(nb)) if nb else 0.0
            if split < THETA:
                lost += 0.0 if n in seeds else score[n]
                continue
            for o, t, label, w, conf in nb:
                if o in block or o in seeds or (types and t not in types):
                    continue
                g = ALPHA * split * w * prof.get(t, 1.0) * damp(m, o)
                if g < THETA:
                    lost += g
                    continue
                score[o] = score.get(o, 0.0) + g
                if g > best.get(o, 0.0):
                    best[o], via[o] = g, (n, label, conf)
                if o not in reached:
                    reached.add(o)
                    nxt.append(o)
        frontier = nxt
    ranked = sorted(((n, s) for n, s in score.items() if n not in seeds), key=lambda x: (-x[1], x[0]))
    return ranked, via, lost


def short(m, nid):
    kind, _, key = nid.partition(":")
    return f"{m.nodes[nid]['kind'] if nid in m.nodes else kind} {key}"


def describe(m, nid, cut=90):
    n = m.nodes[nid]
    bits = [short(m, nid)] + ([n["l0"][:cut]] if n["l0"] else [])
    if n["kind"] == "spec":
        bits += [x for x in (n["status"][:40], "Tech " + n["extra"]["tech"] if n["extra"].get("tech") else "") if x]
    return " \u00b7 ".join(bits)


def detail(m, nid, level):
    """The indented lines a node adds at level 1 or 2."""
    n, x, out = m.nodes[nid], m.nodes[nid]["extra"], []
    if level < 1:
        return out
    if n["kind"] == "spec":
        rules = x.get("rules", [])
        cap = 5 if level == 1 else 12
        out += [f"rule {i}: {r}" for i, r in enumerate(rules[:cap], 1)]
        if len(rules) > cap:
            out.append(f"+{len(rules) - cap} more rules (--level 2)")
        if level == 2:
            out += [f"{n['path']}:{a}-{b} {title}" for title, a, b in x.get("sections", [])]
    elif n["kind"] in ("file", "test"):
        names = [what for _, what in x.get("outline", [])]
        if level == 1 and names:
            out.append(", ".join(w.split()[-1] for w in names[:12]) + (f" +{len(names) - 12}" if len(names) > 12 else ""))
        elif level == 2:
            out += [f"{n['path']}:{line} {what}" for line, what in x.get("outline", [])[:15]]
    elif n["kind"] == "dec":
        out.append(x.get("text", "")[:240 if level == 1 else 600])
    elif n["kind"] == "mod":
        members = sorted(o for o, t, label, *_ in m.adj[nid] if label == "has")
        out.append(f"{len(members)} members: " + ", ".join(short(m, o) for o in members[:8]))
    elif n["kind"] == "area":
        out.append(f"maps {sum(1 for o, t, label, *_ in m.adj[nid] if label == 'maps')} files ({n['path']})")
    return [ln for ln in out if ln]


def reads(m, nid):
    """Level 3: what to read for a node, as `read path:a-b (what)` lines."""
    n, x = m.nodes[nid], m.nodes[nid]["extra"]
    if not n["path"]:
        return []
    if n["kind"] == "spec":
        keep = [s for s in x.get("sections", []) if s[0].lower().startswith(("what must be true", "how to build"))]
        return [f"read {n['path']}:{a}-{b} ({title})" for title, a, b in keep] or [f"read {n['path']}"]
    if n["kind"] in ("file", "test"):
        lines = x.get("lines")
        if lines and lines > 300:
            return [f"read {n['path']} ({lines} lines: map.py first, then the range)"]
        return [f"read {n['path']}" + (f" ({lines} lines)" if lines else "")]
    return [f"read {n['path']}:{x['line']}" if x.get("line") else f"read {n['path']}"]


def chain(m, via, seeds, nid):
    hops, cur = [], nid
    while cur not in seeds and cur in via and len(hops) < 4:
        frm, label, conf = via[cur]
        hops.append((frm, label + MARK.get(conf, ""), cur))
        cur = frm
    return " ".join([short(m, hops[-1][0])] + [f"-{label}-> {short(m, to)}" for _, label, to in reversed(hops)])


def session_file(root, session):
    sid = re.sub(r"[^\w-]", "", session or "")
    return main_tree(root) / ".work" / "kmap" / "seen" / f"{sid}.json" if sid else None


def query(root, m, a):
    state, ids = resolve(m, a.topic)
    if state != "ok":
        return refuse(state, a.topic, ids)
    seed = ids[0]
    ranked, via, lost = flow(m, ids, a.depth, a.role, set(a.types.split(",")) if a.types else None)
    sf = session_file(root, a.session)
    seen = json.loads(sf.read_text(encoding="utf-8")) if sf and sf.is_file() else {}
    asks, shown_at, paths = seen.get("asks", {}), seen.get("nodes", {}), []
    key = f"{seed}#{a.level}"
    asks[key] = asks.get(key, 0) + 1
    budget = a.budget or BUDGET[a.level]
    loop = (f"LOOP: {seed} asked {asks[key]} times at level {a.level} this session: read its --level 3 list, or "
            "kmap.py miss \"<what you need>\" <where you found it>") if asks[key] >= 3 else ""
    if a.json:
        shown = ranked[:budget]
        print(json.dumps({"seed": seed, "node": m.nodes[seed], "reached": len(ranked), "lost": round(lost, 3),
                          "loop": loop, "nodes": [{"id": n, "score": round(s, 3), "l0": m.nodes[n]["l0"],
                                                   "via": via[n][0], "label": via[n][1]} for n, s in shown]}, indent=1))
        paths = [m.nodes[n]["path"] for n in [seed] + [n for n, _ in shown]]
        shown_ids = [n for n, _ in shown]
    elif a.level == 3:
        lines, shown_ids = [loop] if loop else [], []
        for nid in [seed] + [n for n, _ in ranked]:
            for r in reads(m, nid):
                if r not in lines and len(lines) < budget - 1:
                    lines.append(r)
                    paths.append(m.nodes[nid]["path"])
                    shown_ids.append(nid)
        lines.append(f"-- {sum(ln.startswith('read ') for ln in lines)} reads for {seed}, from the top of {len(ranked)}"
                     " related; read by range, and the code wins over any doc")
        print("\n".join(lines))
    else:
        lines = ([loop] if loop else []) + [describe(m, seed, 120) + (
            " \u00b7 not " + ", ".join(m.nodes[seed]["extra"]["not"]) if m.nodes[seed]["extra"].get("not") else "")]
        lines += [" " + d for d in detail(m, seed, a.level)]
        paths.append(m.nodes[seed]["path"])
        shown_ids = []
        for nid, s in ranked:
            frm, label, conf = via[nid]
            how = label + MARK.get(conf, "") + ("" if frm in ids else f" of {short(m, frm)}")
            if shown_at.get(nid, -1) >= a.level:
                row = [f"{s:4.2f} (seen) {short(m, nid)}"]
            else:
                row = [f"{s:4.2f} {describe(m, nid)} \u00b7 via {how}"] + ["      " + d for d in detail(m, nid, a.level)]
                if a.why and frm not in ids:
                    row.append("      why: " + chain(m, via, ids, nid))
            if len(lines) + len(row) > budget - 1:
                row = row[:1]                     # no room for its facts: its line alone
                if len(lines) + 1 > budget - 1:
                    break
            lines += row
            shown_ids.append(nid)
            paths.append(m.nodes[nid]["path"])
        if not ranked:
            leaf = reads(m, seed)
            lines.append(f"-- leaf: nothing links here above {THETA}; " + (leaf[0] if leaf else "read the code"))
        else:
            lines.append(f"-- {len(shown_ids)} shown of {len(ranked)} reached (depth {a.depth}); "
                         f"{len(ranked) - len(shown_ids)} cut by the budget, mass {lost:.2f} fell under {THETA}. next: "
                         + ("--level 1 for facts · " if a.level == 0 else "")
                         + "--level 3 for what to read · kmap.py miss \"<what>\" <where> when the map lacked it")
        print("\n".join(lines))
    if sf:
        sf.parent.mkdir(parents=True, exist_ok=True)
        for nid in shown_ids:
            shown_at[nid] = max(shown_at.get(nid, -1), a.level)
        seen = {"asks": asks, "nodes": shown_at, "paths": sorted(set(seen.get("paths", [])) | {p for p in paths if p})}
        sf.write_text(json.dumps(seen), encoding="utf-8")
    return 0


def find_path(m, a_id, b_id, hops=4):
    """The strongest chain of at most `hops` links, scored like one strand of the flow (decay, split by the node's
    degree, hub damping): (score, [nodes], [labels])."""
    best = {a_id: (1.0, [a_id], [])}
    frontier = [a_id]
    for _ in range(hops):
        nxt = []
        for n in frontier:
            p, nodes, labels = best[n]
            split = p / math.sqrt(max(1, m.deg.get(n, 1)))
            for o, t, label, w, conf in m.adj.get(n, []):
                q = ALPHA * split * w * damp(m, o)
                if o not in best or q > best[o][0]:
                    best[o] = (q, nodes + [o], labels + [label])
                    nxt.append(o)
        frontier = nxt
    return best.get(b_id)


def cmd_path(m, a):
    got = []
    for t in (a.a, a.b):
        state, ids = resolve(m, t)
        if state != "ok":
            return refuse(state, t, ids)
        got.append(ids[0])
    found = find_path(m, *got)
    if not found or found[0] < THETA:
        print(f"no path above {THETA} in 4 hops between {short(m, got[0])} and {short(m, got[1])}: unrelated for this "
              "job. If they are related: kmap.py miss \"<why>\" <file>")
        return 0
    p, nodes, labels = found
    print(f"{p:.3f} " + short(m, nodes[0]) + "".join(f" -{lab}-> {short(m, n)}" for lab, n in zip(labels, nodes[1:])))
    hubs = [short(m, n) for n in nodes[1:-1] if m.deg.get(n, 0) > HUB_MIN]
    if p < WEAK:
        print(f"weak (under {WEAK}): " + (f"only through the hub {', '.join(hubs)}; " if hubs else "")
              + "probably unrelated for this job")
    return 0


def cmd_impact(m, a):
    state, ids = resolve(m, a.topic)
    if state != "ok":
        return refuse(state, a.topic, ids)
    nid = ids[0]
    inbound = collections.defaultdict(set)
    for s, t, d, w, conf, ev in m.edges:
        if d == nid:
            inbound[REVERSE.get(t, t)].add(s)
        elif s == nid and t in ("tested-by", "co-changes", "same-files-as", "shares-ui", "conflicts"):
            inbound[t].add(d)
    print(describe(m, nid, 120))
    for label in sorted(inbound, key=lambda k: (-len(inbound[k]), k)):
        items = sorted(inbound[label])
        print(f" {label} ({len(items)}): " + ", ".join(short(m, x) for x in items[:a.budget]) +
              (f" +{len(items) - a.budget}" if len(items) > a.budget else ""))
    if not inbound:
        print(" nothing links to it")
    return 0


def golden(root, m):
    """`docs/kmap/golden.md` lines `` - `topic` has `a` `b`; not `c` `` -> red lines (top 20 at depth 2)."""
    reds = []
    path = root / "docs" / "kmap" / "golden.md"
    if not path.is_file():
        return reds
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.lstrip().startswith(("- `", "* `")):
            continue
        head, _, rest = line.partition(" has ")
        must, _, never = rest.partition(" not ")
        if not rest:
            head, _, never = line.partition(" not ")
        topic = TICK.findall(head)
        if not topic:
            continue
        where = f"docs/kmap/golden.md:{number}"
        state, ids = resolve(m, topic[0])
        if state != "ok":
            reds.append(f"golden: {where} topic `{topic[0]}` is {state}")
            continue
        top = {n for n, _ in flow(m, ids, 2)[0][:20]}
        for want, tokens in ((True, TICK.findall(must)), (False, TICK.findall(never))):
            for tok in tokens:
                st, got = resolve(m, tok)
                if st != "ok":
                    reds.append(f"golden: {where} `{tok}` is {st}")
                elif (got[0] in top) != want:
                    reds.append(f"golden: {where} `{topic[0]}` {'misses' if want else 'reaches'} `{tok}` in its top 20")
    return reds


def cmd_check(root, m, a):
    reds = [t for level, t in m.issues if level == "red"] + golden(root, m)
    warns = [t for level, t in m.issues if level == "warn"]
    degs = sorted(m.deg.values())
    if degs:
        cut = max(HUB_MIN, degs[min(len(degs) - 1, int(len(degs) * 0.99))])
        warns += [f"hub: {short(m, n)} has {d} links (damped in every answer; split it, or keep it as a hub)"
                  for n, d in sorted(m.deg.items(), key=lambda kv: -kv[1]) if d > cut][:10]
    mods = sorted(n for n in m.nodes if n.startswith("mod:"))
    if mods:
        member = {o for mod in mods for o, t, label, *_ in m.adj[mod] if label == "has"}
        warns += [f"no module: {short(m, n)}" for n, x in sorted(m.nodes.items())
                  if x["kind"] == "spec" and x["status"] != "done" and x["path"] and n not in member][:20]
        warns += [f"empty module: {short(m, mod)} has no member" for mod in mods
                  if not any(label == "has" for _, t, label, *_ in m.adj[mod])]
    for r in reds:
        print("RED  " + r)
    for w in warns:
        print("WARN " + w)
    print(f"kmap check: {len(reds)} red, {len(warns)} warn ({len(m.nodes)} nodes, {len(m.edges)} links, "
          f"built in {m.meta.get('seconds', '?')} s)")
    return 1 if reds else 0


def cmd_stats(m, a):
    kinds = collections.Counter(n["kind"] for n in m.nodes.values())
    types = collections.Counter(t for _, t, *_ in m.edges)
    hubs = sorted(m.deg.items(), key=lambda kv: -kv[1])[:8]
    if a.json:
        print(json.dumps({"nodes": dict(kinds), "edges": dict(types), "hubs": hubs, "seconds": m.meta.get("seconds")}))
    else:
        print(f"nodes {len(m.nodes)}: " + " ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
        print(f"links {len(m.edges)}: " + " ".join(f"{k}={v}" for k, v in sorted(types.items())))
        print("hubs: " + ", ".join(f"{short(m, n)} {d}" for n, d in hubs) + f"; built in {m.meta.get('seconds')} s")
    return 0


def cmd_miss(root, a):
    log = main_tree(root) / ".work" / "log" / "kmap.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    clean = re.sub(r"\s+", " ", a.need.replace('"', "'")).strip()[:300]
    where = re.sub(r"\s+", "_", a.where.strip())[:200]
    line = f"{time.strftime('%Y-%m-%d %H:%M')} MISS need=\"{clean}\" found={where}" + (f" role={a.role}" if a.role else "")
    new = not log.is_file()
    with log.open("a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write("# kmap log\n\nMISS lines from `kmap.py miss`: a link the map lacked. Local, git-ignored.\n\n")
        f.write(line + "\n")
    print("logged: " + line)
    return 0


def cmd_export(root, m, a):
    out = Path(a.vault).resolve() / "mesh"
    out.mkdir(parents=True, exist_ok=True)
    stamp_file = out / ".kmap-export.json"
    stamps = json.loads(stamp_file.read_text(encoding="utf-8")) if stamp_file.is_file() else {}
    wrote = kept = 0
    index = ["# The knowledge map", "", "Written by `tools/work/kmap.py export`; a note you edit here is kept.", ""]
    for nid, n in sorted(m.nodes.items()):
        if n["kind"] not in ("mod", "spec", "area"):
            continue
        kind, key = nid.split(":", 1)
        rel = f"{kind}/{key.replace('/', '__')}.md"
        links, files = [], []
        for o, t, label, w, conf in sorted(m.adj[nid], key=lambda e: (-e[3], e[0])):
            ok, okey = o.split(":", 1)
            if ok in ("mod", "spec", "area"):
                links.append(f"- {label} [[mesh/{ok}/{okey.replace('/', '__')}|{okey}]]" +
                             (" (curated)" if conf == "CURATED" else ""))
            elif ok in ("file", "test") and len(files) < 40:
                files.append(f"- {label} `{okey}`")
        body = "\n".join(["---", f"kind: {kind}", f"status: {json.dumps(n['status'])}" if n["status"] else "status: \"\"",
                          f"source: {json.dumps(n['path'] or '')}", "---", "", f"# {key}", "", n["l0"], "",
                          "## Links", *(sorted(set(links)) or ["- none"]), "", "## Files", *(files or ["- none"]), ""])
        target = out / rel
        index.append(f"- [[mesh/{rel[:-3]}|{key}]] ({kind})")
        if target.is_file() and stamps.get(rel) != hashlib.sha1(target.read_bytes()).hexdigest():
            kept += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8", newline="\n")
        stamps[rel] = hashlib.sha1(target.read_bytes()).hexdigest()
        wrote += 1
    (out / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8", newline="\n")
    stamp_file.write_text(json.dumps(stamps, indent=0, sort_keys=True), encoding="utf-8")
    print(f"export: {wrote} notes written, {kept} kept (edited in the vault) in {out}")
    return 0


SUBCOMMANDS = {"near", "path", "impact", "miss", "check", "build", "stats", "export"}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root_ap = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    root_ap.add_argument("--root", default=str(HERE.parents[1]), help="the project (default: two folders up)")
    known, rest = root_ap.parse_known_args(argv)
    root = Path(known.root).resolve()
    sub = rest[0] if rest and rest[0] in SUBCOMMANDS else None
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
        ap.add_argument("need")
        ap.add_argument("where")
        ap.add_argument("--role", choices=sorted(ROLES))
    elif sub == "build":
        ap.add_argument("--force", action="store_true")
    elif sub == "stats":
        ap.add_argument("--json", action="store_true")
    elif sub == "export":
        ap.add_argument("--vault", required=True)
    a = ap.parse_args(rest[1:] if sub else rest)
    if sub == "miss":
        return cmd_miss(root, a)
    m = open_map(root, force=sub == "build" and a.force)
    if sub == "build":
        print(f"kmap: {len(m.nodes)} nodes, {len(m.edges)} links, built in {m.meta.get('seconds')} s")
        return 0
    if sub == "near":
        hits = near(m, a.words)
        print("\n".join(describe(m, h) for h in hits) or "nothing near it")
        return 0 if hits else 4
    return {"path": lambda: cmd_path(m, a), "impact": lambda: cmd_impact(m, a), "check": lambda: cmd_check(root, m, a),
            "stats": lambda: cmd_stats(m, a), "export": lambda: cmd_export(root, m, a),
            None: lambda: query(root, m, a)}[sub]()


if __name__ == "__main__":
    sys.exit(main())
