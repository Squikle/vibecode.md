#!/usr/bin/env python3
"""The docs of a git revision -> an Obsidian vault (any version; plain Markdown and frontmatter). Zero LLM, stdlib only.

python -I -X utf8 tools/vault/vaultify.py [--rev origin/main] [--vault <dir>] [--repo <dir>] [--if-changed] [--src <dir>]

Reads only git (`git archive <rev> docs AGENTS.md`); `--src <dir>` reads a plain folder instead (tests). Writes only under
the vault (default `<main tree>/.work/vault`, git-ignored): `docs/**` mirrored, one note per batch (`batches/`), decision
(`decisions/`), open question and known red (`questions/`) and merge (`merges/`), the hub `index.md`, and
`.vaultify/manifest.json` (path -> hash of what it last wrote) and `build.json` (the rev and this script's hash:
`--if-changed` exits at once when both match). A note is rewritten only when its bytes change (the `rev:` line aside);
one this script did not write, or that you edited since, is never overwritten or deleted (`kept_edited`).
Parsed shapes are the template's: the tables of BACKLOG, DECISIONS and QUESTIONS, MERGES' `### date · batch · sha`
entries, and `docs/specs/**/<id>.md`. A row of a parsed doc that fits no shape is a miss (`miss:` lines on stderr):
the librarian fixes the row or this script. Prints one JSON line (`written`, `deleted`, `kept_edited`, `misses`, ...);
exit 1 when misses > 0, 2 on bad arguments. `tools/ci/gate.py ship` and `tools/vault/hook.py` run it.
"""
import argparse, hashlib, io, json, re, subprocess, sys, tarfile, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TABLES = {  # doc -> (the header's first cells, the note folder) for each table it holds
    "docs/BACKLOG.md": [(("#", "Batch"), "batches")],
    "docs/DECISIONS.md": [(("#", "Decision"), "decisions")],
    "docs/QUESTIONS.md": [(("Question",), "questions"), (("Test",), "questions")],
}
MERGE = re.compile(r"^### (\d{4}-\d\d-\d\d(?:[ T]\d\d:\d\d)?) \S (.+?) \S ([0-9a-f]{7,40})\s*$")
ID = re.compile(r"`([\w.-]+)`")


def h12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def stable(data: bytes) -> bytes:
    """A note's bytes without its `rev:` line: a new rev alone rewrites nothing."""
    return re.sub(rb"(?m)^rev: .*\n", b"", data)


def git(repo, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True)


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
        hub = [f"# The docs at {self.rev}", "", f"Built {time.strftime('%Y-%m-%d %H:%M')}; misses {len(self.miss)}.", ""]
        for g in ("batches", "merges", "decisions", "questions", "docs", "top"):
            if g in groups:
                hub += [f"## {g} ({len(groups[g])})", ""] + [f"- [[{r[:-3]}]]" for r in sorted(groups[g])[:200]] + [""]
        self.out["index.md"] = ("\n".join(hub) + "\n").encode("utf-8")


def reconcile(vault: Path, out: dict):
    """Write what changed, keep what is not ours or was edited, delete what vanished. Returns the counts."""
    mp = vault / ".vaultify" / "manifest.json"
    old = json.loads(mp.read_text(encoding="utf-8")) if mp.is_file() else {}
    new, written, kept, deleted = {}, 0, 0, 0
    for rel, data in out.items():
        p = vault / rel
        if p.is_file():
            cur = p.read_bytes()
            if stable(cur) == stable(data) and rel != "index.md":
                new[rel] = h12(cur)
                continue
            if old.get(rel) != h12(cur):  # ours but edited, or never ours: leave it alone
                kept += 1
                if rel in old:
                    new[rel] = old[rel]
                continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        new[rel], written = h12(data), written + (rel != "index.md")
    for rel, hsh in old.items():
        p = vault / rel
        if rel in out or not p.is_file():
            continue
        if h12(p.read_bytes()) == hsh:
            p.unlink()
            deleted += 1
        else:
            kept += 1
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(new, sort_keys=True, indent=0) + "\n", encoding="utf-8")
    return written, deleted, kept


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
    ap.add_argument("--rev", default="origin/main")
    ap.add_argument("--vault", help="default: <main tree>/.work/vault")
    ap.add_argument("--repo", default=str(HERE.parents[1]))
    ap.add_argument("--if-changed", action="store_true")
    ap.add_argument("--src", help="test hook: a plain folder instead of git")
    a = ap.parse_args(argv)  # an unknown argument exits 2
    t0 = time.perf_counter()
    tool = h12(Path(__file__).read_bytes())
    if a.src:
        rev = "src"
    else:
        r = git(a.repo, "rev-parse", "--short=8", "--verify", a.rev + "^{commit}")
        if r.returncode:
            raise SystemExit("git rev-parse failed: " + r.stderr.decode("utf-8", "replace").strip())
        rev = r.stdout.decode().strip()
    if a.vault:
        vault = Path(a.vault).resolve()
    else:
        common = git(a.repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.decode().strip()
        vault = (Path(common).parent if common else Path(a.repo)) / ".work" / "vault"
    state = vault / ".vaultify" / "build.json"
    if a.if_changed and not a.src and state.is_file():
        last = json.loads(state.read_text(encoding="utf-8"))
        if last.get("rev") == rev and last.get("tool") == tool:
            print(json.dumps({"rev": rev, "skipped": True, "written": 0, "misses": 0}))
            return 0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(a.src) if a.src else Path(tmp)
        if not a.src:
            source_tree(a.repo, rev, tmp)
        b = Build(root, rev)
        b.run()
    written, deleted, kept = reconcile(vault, b.out)
    state.write_text(json.dumps({"rev": rev, "tool": tool}) + "\n", encoding="utf-8")
    for x in b.miss[:8]:
        print(f"miss: {x}", file=sys.stderr)
    print(json.dumps({"rev": rev, "skipped": False, "seconds": round(time.perf_counter() - t0, 2), "files": len(b.out),
                      "written": written, "deleted": deleted, "kept_edited": kept, "misses": len(b.miss),
                      "batches": len(b.batches)}))
    return 1 if b.miss else 0


if __name__ == "__main__":
    sys.exit(main())
