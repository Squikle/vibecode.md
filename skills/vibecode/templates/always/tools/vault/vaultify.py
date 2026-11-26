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
"""
import argparse, io, json, re, subprocess, sys, tarfile, tempfile, time
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


class Build:
    def __init__(self, root: Path, rev: str):
        self.root, self.rev, self.out, self.miss, self.batches = root, rev, {}, [], {}

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

    def run(self):
        self.mirror(); self.rows(); self.specs(); self.merges()
        for bid, b in sorted(self.batches.items()):
            links = [f"- Spec: [[{b['spec']}]]" if b.get("spec") else "", f"- Merged: {b['merged']} ({b['sha']})"
                     if b.get("merged") else ""]
            self.add(f"batches/{bid}.md", {k: v for k, v in b.items() if k != "id"} | {"id": bid},
                     f"# {bid}: {b.get('title', '')}\n\n{b.get('what', '')}\n\n" + "\n".join(x for x in links if x))
        groups = {}
        for rel in self.out:
            groups.setdefault(rel.split("/")[0] if "/" in rel else "top", []).append(rel)
        hub = [f"# The docs at {self.rev}", "", f"Built {time.strftime('%Y-%m-%d %H:%M')}; misses {len(self.miss)}.", "",
               "![[Now]]", ""]
        for g in ("batches", "merges", "decisions", "questions", "docs", "top"):
            if g in groups:
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
    ap.add_argument("--resolve", metavar="PATH", help="a conflict merged by hand: take the file back (`.vaultify/conflicts/`)")
    a = ap.parse_args(argv)  # an unknown argument exits 2
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
    state = vault / ".vaultify" / "build.json"
    if a.if_changed and not a.src and state.is_file():
        last = json.loads(state.read_text(encoding="utf-8"))
        if last.get("rev") == rev and last.get("tool") == tool:
            print(json.dumps({"rev": rev, "skipped": True, "written": 0, "misses": 0}))
            return 1 if alert_path(vault).is_file() else 0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(a.src) if a.src else Path(tmp)
        if not a.src:
            source_tree(a.repo, rev, tmp)
        b = Build(root, rev)
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
    state.write_text(json.dumps({"rev": rev, "tool": tool}) + "\n", encoding="utf-8")
    alert(vault, rev, sync.conflicts, b.miss)
    for kind, items in (("miss", b.miss), ("conflict", sync.conflicts)):
        for x in items[:8]:
            print(f"{kind}: {x}", file=sys.stderr)
    print(json.dumps({"rev": rev, "skipped": False, "seconds": round(time.perf_counter() - t0, 2), "files": len(b.out),
                      "written": sync.written, "merged": sync.merged, "conflicts": len(sync.conflicts),
                      "deleted": sync.deleted, "misses": len(b.miss), "batches": len(b.batches)}))
    return 1 if b.miss or sync.conflicts else 0


def vault_dir(a) -> Path:
    if a.vault:
        return Path(a.vault).resolve()
    common = git(a.repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.decode().strip()
    return (Path(common).parent if common else Path(a.repo)) / ".work" / "vault"


if __name__ == "__main__":
    sys.exit(main())
