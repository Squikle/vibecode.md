#!/usr/bin/env python3
"""The docs of a git revision -> an Obsidian vault (any version; plain Markdown and frontmatter). Zero LLM, stdlib only.

python -I -X utf8 tools/vault/vaultify.py [--rev main] [--vault <dir>] [--repo <dir>] [--if-changed] [--src <dir>]
python -I -X utf8 tools/vault/vaultify.py [--vault <dir>] --resolve <path>   (a conflict you merged by hand, taken back)

Reads only git (`git archive <rev> docs AGENTS.md`); `--src <dir>` reads a plain folder instead (tests). Writes only under
the vault (default `<main tree>/.work/vault`, git-ignored) and `vault-alert.json` next to it: `docs/**` mirrored, one note
per batch (`batches/`), decision (`decisions/`), open question and known red (`questions/`) and merge (`merges/`), the hub
`index.md`, `Now.md` once as a stub (`vault_now.py` writes it; never in the manifest, never deleted), and
`.vaultify/manifest.json` (path -> hash of what it last wrote or merged), `base/` and `conflicts/` (vault_merge.py) and
`build.json` (the rev and the hash of this folder's scripts: `--if-changed` exits at once when both match). A file is
rewritten only when its bytes change (the `rev:` line and the hub's build time aside); one you edited since gets the new
docs by a 3-way merge, or is a conflict: left as it is, parked, counted, shown at the hub's top and in the alert.
Parsed shapes are the template's: the tables of BACKLOG, DECISIONS and QUESTIONS, MERGES' `### date · batch · sha`
entries, and `docs/specs/**/<id>.md`. A row of a parsed doc that fits no shape is a miss (`miss:` lines on stderr):
the librarian fixes the row or this script. Prints one JSON line (`written`, `merged`, `conflicts`, `deleted`, `misses`,
...); exit 1 when misses or conflicts > 0, 2 on bad arguments. `tools/ci/gate.py ship` and `tools/vault/hook.py` run it.
Logs (`--logs <file or folder>`, repeatable): one line per event, `- YYYY-MM-DD [hh:mm] [TYPE] text key=value`, each a
note under `log/<log>/` with its fields as properties (README "Logs"). The committed ones, `docs/log/**/*.md`, are read
from git like every doc (a bad line is a miss); the local ones default to the main tree's `.work/coordinator-log.md` and
`.work/log/` (not with `--src`; a bad line there is a `log_misses` count, never the exit code).
"""
import argparse, datetime, io, json, re, subprocess, sys, tarfile, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))              # `python -I` leaves the script's folder off sys.path
from vault_merge import NO_WINDOW, Sync, alert, alert_path, callout, h12, resolve as take_back, stable   # noqa: E402,F401

HUB, NOW = "index.md", "Now.md"
NOW_STUB = ('---\ntags:\n  - "vault/now"\n---\n# Now\n\nThe coordinator\'s jobs show here once '
            '`tools/vault/vault_now.py` has run (`tools/vault/README.md`).\n')
TABLES = {  # doc -> (the header's first cells, the note folder) for each table it holds
    "docs/BACKLOG.md": [(("#", "Batch"), "batches")],
    "docs/DECISIONS.md": [(("#", "Decision"), "decisions")],
    "docs/QUESTIONS.md": [(("Question",), "questions"), (("Test",), "questions")],
}
MERGE = re.compile(r"^### (\d{4}-\d\d-\d\d(?:[ T]\d\d:\d\d)?) \S (.+?) \S ([0-9a-f]{7,40})\s*$")
ID = re.compile(r"`([\w.-]+)`")
LOG_DIR = "docs/log"                        # the committed logs; the local ones come by --logs
LOCAL_LOGS = (".work/coordinator-log.md", ".work/log")      # the default local ones, under the main tree
LOG_KEEP = 2000                             # notes per log, the newest; its index note counts them all
LOG_NEWEST = 30                             # events listed on a log's index note
LOG_START = re.compile(r"^(?:[-*] +)?\d{4}-\d\d-\d\d")      # a line that means to be an event
LOG_LINE = re.compile(r"^(?:[-*] +)?(\d{4}-\d\d-\d\d)(?:[ T](\d\d):(\d\d)(?::\d\d)?)?(?:\s+(.*?))?\s*$")
LOG_TYPE = re.compile(r"([A-Z][A-Z0-9_-]+)(?!\w)(?:\s*:)?\s*")  # the first word in capitals is the type; none: `note`
KV = re.compile(r'(?<![^\s(])([a-z][a-z0-9_]*)=("[^"]*"|[^\s"]+)')
TAG = re.compile(r"(?<![\w&/#])#([A-Za-z][\w/-]*)")
NUMBER = re.compile(r"-?(?:0|[1-9]\d{0,11})(?:\.\d+)?")
TEXT_KEYS = {"sha", "id", "rev", "commit", "batch", "version"}   # digits there are a name, not a number
OWN_KEYS = {"type", "date", "day", "log", "tags", "source", "rev", "batches"}  # the build's; such a key stays in the text
FENCE = re.compile(r"^\s*(```|~~~)")
VIEWS = {  # Bases (Obsidian 1.9+, a core plugin): file -> (folder, [(name, columns, group, sort, filters)])
    "Batches.base": ("batches", [
        ("Open", "file.name, note.title, note.status, note.waits_on", None, None, ['note.status != "merged"']),
        ("By status", "file.name, note.title, note.waits_on, note.merged", "note.status", None, []),
        ("Merged", "note.merged, file.name, note.title, note.sha", None, "note.merged", ['note.status == "merged"'])]),
    "Merges.base": ("merges", [("Newest", "note.date, file.name, note.sha", None, "note.date", [])]),
    "Decisions.base": ("decisions", [("All", "note.number, file.name", None, None, [])]),
    "Questions.base": ("questions", [("By kind", "file.name, note.source", "note.kind", None, [])]),
    "Logs.base": ("log", [  # the events; a log's index note (`log/<name>.md`) sits one folder up
        ("Newest", "note.date, note.type, note.log, file.name", None, "note.date", ['file.folder != "log"']),
        ("By type", "note.date, note.log, file.name", "note.type", "note.date", ['file.folder != "log"']),
        ("By day", "note.date, note.type, note.log, file.name", "note.day DESC", "note.date", ['file.folder != "log"']),
        ("Reds by test", "note.date, note.part, note.machine, note.sha", "note.test", "note.date",
         ['file.folder != "log"', 'note.type == "red"']),
        ("Flaky by test", "note.date, note.part, note.machine, note.sha", "note.test", "note.date",
         ['file.folder != "log"', 'note.type == "flaky"'])]),
}


def git(repo, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, creationflags=NO_WINDOW)


def default_rev(repo) -> str:
    """The gate's base rule (`tools/ci/gate.py` `default_base`, kept apart: this folder also runs copied alone, as
    `integrate.py`'s vault check): local `main`, else `origin/main`."""
    return "main" if git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/main").returncode == 0 else "origin/main"


def tool_hash() -> str:
    return h12(b"".join(p.name.encode() + p.read_bytes() for p in sorted(HERE.glob("*.py"))))


def slug(text: str) -> str:
    return re.sub(r"[^\w-]+", "-", text.lower()).strip("-")[:60] or "x"


def note(meta: dict, body: str) -> bytes:
    lines = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" if not isinstance(v, str) or k != "rev" else f"rev: {v}"
                       for k, v in meta.items() if v not in (None, "")] + ["---", ""]
    return ("\n".join(lines) + body.rstrip() + "\n").encode("utf-8")


def cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def tables(text: str):
    """Each Markdown table: (header cells, [(line number, row cells)])."""
    lines, i = text.splitlines(), 0
    while i < len(lines) - 1:
        if lines[i].lstrip().startswith("|") and re.fullmatch(r"\s*\|[\s:|-]+\|\s*", lines[i + 1]):
            head, rows, i = cells(lines[i]), [], i + 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append((i + 1, cells(lines[i])))
                i += 1
            yield head, rows
        else:
            i += 1


def value(key: str, raw: str):
    if raw.startswith('"'):
        return raw[1:-1]
    raw = raw.rstrip(".,;)")
    if key in TEXT_KEYS:
        return raw
    if raw in ("true", "false"):
        return raw == "true"
    if NUMBER.fullmatch(raw):
        return float(raw) if "." in raw else int(raw)
    return raw


def parse_log(text: str):
    """A log's events and misses. An event is a line `[- ]YYYY-MM-DD [hh:mm[:ss]] [TYPE[:]] text`; the text's
    `key=value` / `key="a value"` are its properties (numbers and true/false typed), `#tag`s its tags, backticked ids
    its batches; indented lines under it are its body. A line that starts with a date and is none of that is a miss;
    any other line (headings, prose, fenced code) is not an event."""
    events, misses, fence, last = [], [], False, None
    for n, raw in enumerate(text.splitlines(), 1):
        if FENCE.match(raw):
            fence, last = not fence, None
            continue
        line = raw.strip()
        if fence:
            continue
        if not line:
            last = None
            continue
        if last is not None and raw[:1] in (" ", "\t") and not LOG_START.match(line):
            last["more"].append(line)
            continue
        last = None
        if not LOG_START.match(line):
            continue
        m = LOG_LINE.match(line)
        if not m:
            misses.append((n, "not `YYYY-MM-DD [hh:mm] TYPE text`"))
            continue
        day, hh, mm, rest = m[1], m[2], m[3], m[4] or ""
        try:
            datetime.date.fromisoformat(day)
            if hh and (int(hh) > 23 or int(mm) > 59):
                raise ValueError
        except ValueError:
            misses.append((n, f"no such date or time: {line[:16]!r}"))
            continue
        t = LOG_TYPE.match(rest)
        text_ = rest[t.end():] if t else rest
        if not t and not text_:
            misses.append((n, "a date and nothing else"))
            continue
        props = {}
        for k, v in KV.findall(text_):
            if k not in OWN_KEYS:
                props.setdefault(k, value(k, v))
        last = {"line": n, "raw": line, "day": day, "time": f"{hh}:{mm}" if hh else "", "word": t[1] if t else "",
                "type": t[1].lower() if t else "note", "text": text_, "props": props,
                "tags": sorted(set(TAG.findall(text_))), "ids": list(dict.fromkeys(ID.findall(text_))), "more": []}
        events.append(last)
    return events, misses


def log_name(rel: str) -> str:
    """`ops/deploys.md` -> `ops-deploys`: a log's name, its folder under `log/`."""
    return slug(rel[:-3] if rel.endswith(".md") else rel).replace("_", "-") or "log"


def log_files(given: Path):
    """(name, file) for a log file, or each `*.md` under a log folder (named by its path there)."""
    if given.is_file():
        return [(log_name(given.name), given)]
    return [(log_name(p.relative_to(given).as_posix().replace("/", "-")), p) for p in sorted(given.rglob("*.md"))]


def base_file(folder: str, views) -> bytes:
    out = [f'filters:\n  and:\n    - file.inFolder("{folder}")\nviews:']
    for name, order, group, sort, flt in views:
        out.append(f"  - type: table\n    name: {name}")
        if group:
            prop, _, way = group.partition(" ")
            out.append(f"    groupBy:\n      property: {prop}\n      direction: {way or 'ASC'}")
        if flt:
            out.append("    filters:\n      and:\n" + "\n".join(f"        - '{f}'" for f in flt))
        out.append("    order:\n" + "\n".join(f"      - {c}" for c in order.split(", ")))
        if sort:
            out.append(f"    sort:\n      - property: {sort}\n        direction: DESC")
    return ("\n".join(out) + "\n").encode("utf-8")


def alias(text: str, n: int = 90) -> str:
    text = re.sub(r"[\[\]|]", " ", " ".join(text.split()))
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


class Build:
    def __init__(self, root: Path, rev: str, local=(), home=None):
        self.root, self.rev, self.out, self.miss, self.batches = root, rev, {}, [], {}
        self.local, self.home, self.log_miss, self.log_count = list(local), home, [], {}

    def add(self, rel: str, meta: dict, body: str):
        self.out[rel] = note({**meta, "rev": self.rev}, body)

    def mirror(self):
        for p in sorted(self.root.rglob("*.md")):
            rel = p.relative_to(self.root).as_posix()
            text = p.read_text(encoding="utf-8", errors="replace")
            self.add(rel, {"source": rel, "source_hash": h12(text.encode("utf-8"))}, text)

    def batch(self, bid: str, **meta):
        self.batches.setdefault(bid, {"id": bid}).update({k: v for k, v in meta.items() if v})

    def rows(self):
        for doc, shapes in TABLES.items():
            p = self.root / doc
            if not p.is_file():
                continue
            for head, rows in tables(p.read_text(encoding="utf-8", errors="replace")):
                shape = next((f for first, f in shapes if tuple(head[:len(first)]) == first), None)
                if shape is None:
                    continue
                for n, row in rows:
                    where = f"{doc}:{n}"
                    if len(row) != len(head):
                        self.miss.append(f"{where}: {len(row)} cells, the header has {len(head)}")
                        continue
                    r = dict(zip(head, row))
                    if shape == "batches":
                        m = ID.search(r["Batch"])
                        if not m:
                            self.miss.append(f"{where}: no `id` in the Batch cell")
                            continue
                        self.batch(m[1], status=r.get("Status", "open"), title=ID.sub("", r["Batch"]).strip(),
                                   what=r.get("What"), waits_on=r.get("Waits on"), row=where)
                    elif shape == "decisions":
                        if not re.fullmatch(r"\d+|NEW", r["#"]):
                            self.miss.append(f"{where}: decision number {r['#']!r}")
                            continue
                        self.add(f"decisions/D{r['#']}.md", {"source": where, "number": r["#"]},
                                 f"# D{r['#']}: {r['Decision']}\n\n**Why:** {r.get('Why', '')}\n\n**Where:** {r.get('Where', '')}")
                    else:
                        key = head[0]
                        self.add(f"questions/{slug(r[key])}.md", {"source": where, "kind": key.lower()},
                                 f"# {r[key]}\n\n" + "\n".join(f"- **{k}:** {v}" for k, v in r.items() if k != key))

    def specs(self):
        for p in sorted((self.root / "docs/specs").rglob("*.md")) if (self.root / "docs/specs").is_dir() else []:
            if p.name.startswith("_"):
                continue
            rel = p.relative_to(self.root).as_posix()
            self.batch(p.stem, spec=rel[:-3], status="merged" if "/done/" in rel else None)

    def merges(self):
        p = self.root / "docs/MERGES.md"
        if not p.is_file():
            return
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, ln in enumerate(lines):
            if not ln.startswith("### "):
                continue
            m = MERGE.match(ln)
            if not m:
                self.miss.append(f"docs/MERGES.md:{i + 1}: not `### date time · batch · sha`")
                continue
            when, batch, sha = m.groups()
            body = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("#"):
                    break
                body.append(nxt)
            fields = dict(re.findall(r"^- \*\*(\w+):\*\* (.*)$", "\n".join(body), re.M))
            self.add(f"merges/{when[:10]}-{sha[:8]}.md", {"source": f"docs/MERGES.md:{i + 1}", "date": when, "sha": sha},
                     f"# {batch} ({sha[:8]})\n\n" + "\n".join(f"- **{k}:** {v}" for k, v in fields.items()))
            for bid in ID.findall(batch) or re.split(r",? and |, ", batch):
                self.batch(bid.strip(), merged=when, sha=sha[:8], status="merged")

    def shown(self, p: Path) -> str:
        try:
            return p.resolve().relative_to(Path(self.home).resolve()).as_posix()
        except (TypeError, ValueError):
            return p.as_posix()

    def logs(self):
        """Each log: one note per event, the newest LOG_KEEP, under `log/<name>/`, and its index note `log/<name>.md`
        (counts by type, the newest events, the lines that are not events). A bad line of a committed log is a miss;
        of a local one a `log_misses` count, shown on its index note."""
        found = {}
        given = [(self.root / LOG_DIR, True)] if (self.root / LOG_DIR).is_dir() else []
        for top, committed in given + [(p, False) for p in self.local]:
            for name, p in log_files(top):
                rel = p.relative_to(self.root).as_posix() if committed else self.shown(p)
                events, misses = parse_log(p.read_text(encoding="utf-8", errors="replace"))
                lg = found.setdefault(name, {"events": [], "sources": [], "misses": []})
                lg["sources"].append(rel)
                lg["events"] += [{**e, "source": rel} for e in events]
                for n, why in misses:
                    (self.miss if committed else self.log_miss).append(f"{rel}:{n}: {why}")
                    lg["misses"].append(f"`{rel}:{n}`: {why}")
        for name, lg in sorted(found.items()):
            evs = sorted(lg["events"], key=lambda e: (e["day"], e["time"]))   # stable: a file's order within a minute
            if not evs and not lg["misses"]:
                continue
            named, used = [], set()
            for e in evs:
                when = e["day"] + ("-" + e["time"].replace(":", "") if e["time"] else "")
                stem = f"{when}-{slug(e['type'])[:24]}-{h12(e['raw'].encode('utf-8'))[:6]}"
                k, i = stem, 2
                while k in used:
                    k, i = f"{stem}-{i}", i + 1
                used.add(k)
                named.append((k, e))
            for k, e in named[-LOG_KEEP:]:
                bids = [b for b in e["ids"] if b in self.batches]
                date = f"{e['day']} {e['time']}".strip()
                meta = {"type": e["type"], "date": date, "day": e["day"], "log": name, "tags": e["tags"] or None,
                        "source": e["source"], "batches": bids or None, **e["props"]}
                body = [f"# {e['word'] or 'Note'} {date}", "", e["text"] or "(no text)"] + ([""] + e["more"] if e["more"] else [])
                body += ["", f"- Log: [[log/{name}]]"] + (["- Batches: " + ", ".join(f"[[batches/{b}]]" for b in bids)] if bids else [])
                self.add(f"log/{name}/{k}.md", meta, "\n".join(body))
            counts = {}
            for e in evs:
                c = counts.setdefault(e["type"], [0, ""])
                c[0], c[1] = c[0] + 1, max(c[1], f"{e['day']} {e['time']}".strip())
            first = f"{evs[0]['day']} {evs[0]['time']}".strip() if evs else ""
            last = f"{evs[-1]['day']} {evs[-1]['time']}".strip() if evs else ""
            self.log_count[name] = len(evs)
            head = (f"{len(evs)} events, {first or '-'} to {last or '-'}, from " + ", ".join(f"`{s}`" for s in lg["sources"])
                    + (f"; a note for each of the newest {LOG_KEEP}" if len(evs) > LOG_KEEP else "") + ".")
            body = [f"# Log: {name}", "", head, "", "Every view: [[Logs.base]]", "", "| Type | Events | Last |", "|---|---|---|"]
            body += [f"| {t} | {c[0]} | {c[1]} |" for t, c in sorted(counts.items(), key=lambda kv: (-kv[1][0], kv[0]))]
            body += ["", "## Newest", ""] + [f"- {e['day']} {e['time']} **{e['word'] or 'note'}** [[log/{name}/{k}|{alias(e['text']) or k}]]"
                                             .replace("  **", " **") for k, e in reversed(named[-LOG_NEWEST:])]
            if lg["misses"]:
                body += ["", f"## Not events ({len(lg['misses'])})", "", "Lines that start with a date but fit no event shape "
                         "(`tools/vault/README.md`, Logs):", ""] + [f"- {m}" for m in lg["misses"][:20]]
            self.add(f"log/{name}.md", {"log": name, "events": len(evs), "first": first, "last": last,
                                        "sources": lg["sources"]}, "\n".join(body))

    def run(self):
        self.mirror(); self.rows(); self.specs(); self.merges(); self.logs()
        for view, (folder, views) in VIEWS.items():
            self.out[view] = base_file(folder, views)
        for bid, b in sorted(self.batches.items()):
            links = [f"- Spec: [[{b['spec']}]]" if b.get("spec") else "", f"- Merged: {b['merged']} ({b['sha']})"
                     if b.get("merged") else ""]
            self.add(f"batches/{bid}.md", {k: v for k, v in b.items() if k != "id"} | {"id": bid},
                     f"# {bid}: {b.get('title', '')}\n\n{b.get('what', '')}\n\n" + "\n".join(x for x in links if x))
        groups = {}
        for rel in self.out:
            if rel.endswith(".md"):
                groups.setdefault(rel.split("/")[0] if "/" in rel else "top", []).append(rel)
        hub = [f"# The docs at {self.rev}", "", f"Built {time.strftime('%Y-%m-%d %H:%M')}; misses {len(self.miss)}.", "",
               "Every view (Bases, Obsidian 1.9+): " + " · ".join(f"[[{v}]]" for v in VIEWS), "", "![[Now]]", ""]
        for g in ("batches", "merges", "decisions", "questions", "log", "docs", "top"):
            if g == "log" and self.log_count:
                hub += [f"## logs ({len(self.log_count)})", ""] + [f"- [[log/{n}]] ({c} events)" for n, c in self.log_count.items()] + [""]
            elif g in groups and g != "log":
                hub += [f"## {g} ({len(groups[g])})", ""] + [f"- [[{r[:-3]}]]" for r in sorted(groups[g])[:200]] + [""]
        self.out[HUB] = ("\n".join(hub) + "\n").encode("utf-8")


def source_tree(repo, rev: str, tmp: str):
    top = set(git(repo, "ls-tree", "--name-only", rev).stdout.decode("utf-8", "replace").split("\n"))
    paths = [p for p in ("docs", "AGENTS.md") if p in top]
    if not paths:
        raise SystemExit(f"{rev} has no docs/ and no AGENTS.md")
    r = git(repo, "archive", "--format=tar", rev, *paths)
    if r.returncode:
        raise SystemExit("git archive failed: " + r.stderr.decode("utf-8", "replace").strip())
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
        for m in tf:
            if m.isfile() and not m.name.startswith("/") and ".." not in Path(m.name).parts:
                dest = Path(tmp) / m.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(tf.extractfile(m).read())


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Build the Obsidian vault of the docs (reads git only, writes only the vault).")
    ap.add_argument("--rev", help="git revision to build from (default: local main, else origin/main)")
    ap.add_argument("--vault", help="default: <main tree>/.work/vault")
    ap.add_argument("--repo", default=str(HERE.parents[1]))
    ap.add_argument("--if-changed", action="store_true")
    ap.add_argument("--src", help="test hook: a plain folder instead of git")
    ap.add_argument("--logs", action="append", metavar="PATH",
                    help="a local log file or folder (repeatable; replaces the default .work/coordinator-log.md, .work/log/)")
    ap.add_argument("--resolve", metavar="PATH", help="a conflict merged by hand: take the file back (`.vaultify/conflicts/`)")
    a = ap.parse_args(argv)  # an unknown argument exits 2
    for p in a.logs or ():
        if not Path(p).exists():
            ap.error(f"--logs {p}: no such file or folder")
    t0 = time.perf_counter()
    tool = tool_hash()
    if a.resolve:
        code, res = take_back(vault_dir(a), a.resolve)
        print(json.dumps(res))
        return code
    if a.src:
        rev = "src"
    else:
        r = git(a.repo, "rev-parse", "--short=8", "--verify", (a.rev or default_rev(a.repo)) + "^{commit}")
        if r.returncode:
            raise SystemExit("git rev-parse failed: " + r.stderr.decode("utf-8", "replace").strip())
        rev = r.stdout.decode().strip()
    vault = vault_dir(a)
    home = Path(a.src) if a.src else main_tree(a.repo)
    local = [Path(p) for p in a.logs] if a.logs else [] if a.src else [home / p for p in LOCAL_LOGS if (home / p).exists()]
    files = [p for g in local for _, p in log_files(g)]
    logs = h12(b"".join(p.as_posix().encode("utf-8") + b"\0" + p.read_bytes() for p in files)) if files else ""
    state = vault / ".vaultify" / "build.json"
    if a.if_changed and not a.src and state.is_file():
        last = json.loads(state.read_text(encoding="utf-8"))
        if last.get("rev") == rev and last.get("tool") == tool and last.get("logs", "") == logs:
            print(json.dumps({"rev": rev, "skipped": True, "written": 0, "misses": 0}))
            return 1 if alert_path(vault).is_file() else 0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(a.src) if a.src else Path(tmp)
        if not a.src:
            source_tree(a.repo, rev, tmp)
        b = Build(root, rev, local, home)
        b.run()
    sync = Sync(vault)
    for rel, data in b.out.items():
        if rel != HUB:
            sync.put(rel, data)
    warn = callout(sorted({*sync.conflicts, *sync.vanished(b.out)}), b.miss, vault).encode("utf-8")
    sync.put(HUB, b.out[HUB].replace(b"\n\n", b"\n\n" + warn + b"\n", 1) if warn else b.out[HUB])   # last: its warning
    sync.finish(b.out)                                                                               # names this build's
    if not (vault / NOW).exists():
        (vault / NOW).write_text(NOW_STUB, encoding="utf-8")
    state.write_text(json.dumps({"rev": rev, "tool": tool, "logs": logs}) + "\n", encoding="utf-8")
    alert(vault, rev, sync.conflicts, b.miss)
    for kind, items in (("miss", b.miss), ("conflict", sync.conflicts), ("log-miss", b.log_miss)):
        for x in items[:8]:
            print(f"{kind}: {x}", file=sys.stderr)
    print(json.dumps({"rev": rev, "skipped": False, "seconds": round(time.perf_counter() - t0, 2), "files": len(b.out),
                      "written": sync.written, "merged": sync.merged, "conflicts": len(sync.conflicts),
                      "deleted": sync.deleted, "misses": len(b.miss), "batches": len(b.batches),
                      "log_events": sum(b.log_count.values()), "log_misses": len(b.log_miss)}))
    return 1 if b.miss or sync.conflicts else 0


def main_tree(repo) -> Path:
    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.decode().strip()
    return Path(common).parent if common else Path(repo)


def vault_dir(a) -> Path:
    if a.vault:
        return Path(a.vault).resolve()
    return main_tree(a.repo) / ".work" / "vault"


if __name__ == "__main__":
    sys.exit(main())
