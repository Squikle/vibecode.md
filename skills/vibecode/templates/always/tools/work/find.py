#!/usr/bin/env python3
"""Look up ids and words in the docs instead of `git grep` or reading a doc whole; never hides a hit silently.

`python -I -X utf8 tools/work/find.py <term> [<term>...]`: per term, a status line (an id: its BACKLOG or ROADMAP row,
its spec) and its hits as `:line §section text` (each cut to 200 chars around the hit, a cut marked `…`), grouped by
file with each file's count. Order: what defines it (its BACKLOG/ROADMAP row, its spec, a heading that starts with it),
then a backticked match, a word, a part of a word. About 40 lines in all (`--max N`, `--all`); past them each file's
count still shows, then `+ also in: ...` and `+K more (--all)`.
`--toc <doc>`: its headings with line numbers and bytes. `--section <doc>#<n|heading text|rule n>`: that section only.
`<doc>` is a path or a short name (`PLANNING`, a spec id, `specs`). `--in <doc>...` narrows the search (and opens the
archive folders, e.g. `docs/research`). `--rev <rev>` reads a git revision; the default is the worktree as it is,
uncommitted docs too. Stdlib only; prints to stdout, writes nothing; exit 1 when a term or a section is not found.
"""
import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_LINES = 40          # the default output budget of one run, all terms together
MIN_PER_TERM = 8        # a term's share never goes under this
HIT_CHARS = 200         # one hit line, its `:line §section` prefix included
ALSO_CHARS = 600        # the `+ also in:` line, past which it ends with the count of the files left
DEFINES, ID, WORD, PART = range(4)
# Search order: the working docs first, done specs last; any other folder under docs/ is an archive, searched only via --in.
SPECS, DONE, TOP, ARCHIVE = "docs/specs/", "docs/specs/done/", "docs/*", "archive"
ORDER = ("docs/ROADMAP.md", "docs/BACKLOG.md", SPECS, "docs/QUESTIONS.md", "docs/DECISIONS.md", "docs/HANDOVER.md",
         "docs/PLANNING.md", TOP, "docs/architecture/", "AGENTS.md", "docs/workflow/", DONE, ARCHIVE)
FOLDERS = (DONE, SPECS, "docs/architecture/", "docs/workflow/")
ROWS = ("docs/BACKLOG.md", "docs/ROADMAP.md")  # a table row with the term as a whole cell defines it
KINDS = (".md", ".json")
HEADING = re.compile(r"^(#{1,6}) +(.+?)\s*$")
NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)[.)]?(?:\s|$)")


def git(root, *args, data=None):
    return subprocess.run(["git", "-C", str(root), *args], input=data, capture_output=True, check=False)


def group(path):
    """The index in ORDER of a doc's group."""
    if path in ORDER:
        return ORDER.index(path)
    for prefix in FOLDERS:
        if path.startswith(prefix):
            return ORDER.index(prefix)
    return ORDER.index(TOP) if path.startswith("docs/") and path.count("/") == 1 else ORDER.index(ARCHIVE)


class Docs:
    """The docs, from the worktree or a git revision (one `git cat-file --batch` for all of them)."""

    def __init__(self, root, rev=None):
        self.root, self.rev, self._text = Path(root), rev, {}
        if rev:
            if git(self.root, "rev-parse", "--verify", "--quiet", rev + "^{commit}").returncode:
                raise SystemExit(f"not a revision: {rev}")
            found = git(self.root, "ls-tree", "-r", "-z", "--name-only", rev, "--", "docs", "AGENTS.md").stdout
            found = found.decode("utf-8").split("\0")
        else:
            found = [p.relative_to(self.root).as_posix() for p in (self.root / "docs").rglob("*") if p.is_file()]
            found += ["AGENTS.md"] if (self.root / "AGENTS.md").is_file() else []
        wanted = [p for p in found if p.endswith(KINDS) and (p.startswith("docs/") or p == "AGENTS.md")]
        self.paths = sorted(wanted, key=lambda p: (group(p), p))

    def default(self):
        return [p for p in self.paths if group(p) != ORDER.index(ARCHIVE)]

    def load(self, paths):
        need = [p for p in paths if p not in self._text]
        if need and self.rev:
            out = git(self.root, "cat-file", "--batch", data="".join(f"{self.rev}:{p}\n" for p in need).encode()).stdout
            at = 0
            for p in need:
                nl = out.index(b"\n", at)
                head = out[at:nl].split()
                if len(head) < 3 or head[1] != b"blob":
                    self._text[p], at = "", nl + 1
                    continue
                size = int(head[2])
                self._text[p] = out[nl + 1:nl + 1 + size].decode("utf-8-sig", "replace").replace("\r\n", "\n")
                at = nl + 2 + size
        for p in need if not self.rev else []:
            self._text[p] = (self.root / p).read_text(encoding="utf-8-sig", errors="replace")

    def text(self, path):
        self.load([path])
        return self._text[path]

    def resolve(self, name):
        """The docs a short name means: a path, `PLANNING`, a spec id, `specs` (a folder: all under it)."""
        name = name.replace("\\", "/").strip("/")
        for folder in (name, f"docs/{name}", f"docs/specs/{name}"):
            under = [p for p in self.paths if p.startswith(folder + "/")]
            if under:
                return under
        for p in (name, f"docs/{name}", f"docs/{name}.md", f"docs/{name.upper()}.md", f"docs/specs/{name}.md",
                  f"docs/specs/done/{name}.md", f"docs/architecture/{name}.md", f"docs/workflow/{name}.md", f"{name}.md"):
            if p in self.paths:
                return [p]
        same = [p for p in self.paths if Path(p).stem.lower() == name.lower()]
        return same if len(same) == 1 else []


def headings(lines):
    """[(1-based line, level, title)] of the Markdown headings outside ``` and ~~~ fences."""
    out, fence = [], None
    for n, line in enumerate(lines, 1):
        mark = line.lstrip()[:3]
        if mark in ("```", "~~~"):
            fence = None if fence == mark else (fence or mark)
        elif not fence and (m := HEADING.match(line)):
            out.append((n, len(m.group(1)), m.group(2)))
    return out


def ends(heads, total):
    """The last line of each heading's section: before the next heading of its level or a higher one."""
    return [next((n for n, lv, _ in heads[k + 1:] if lv <= level), total + 1) - 1 for k, (_, level, _) in enumerate(heads)]


def size(lines, a, z):
    return sum(len(line.encode()) + 1 for line in lines[a - 1:z])   # LF, as tools/work/check-docs.py counts


def toc(path, text):
    lines = text.splitlines()
    heads = headings(lines)
    out = [f"{path}: {len(lines)} lines, {size(lines, 1, len(lines))} B"]
    for (n, level, title), z in zip(heads, ends(heads, len(lines))):
        out.append(f"{n:>5}{size(lines, n, z):>8}  {'  ' * (level - 1)}{'#' * level} {title}")
    return out


def plain(title):
    return NUMBERED.sub("", title).strip().lower()


def section(path, text, ref):
    """(found, lines): the section a ref names (its number, its heading text, a part of it), else the numbered rule
    `ref.` (`rule n` goes to the rule at once); a ref that names several sections or none prints the headings."""
    lines, key = text.splitlines(), ref.strip().lower()
    as_rule = re.fullmatch(r"rule\s+(\d+)", key)
    key = as_rule.group(1) if as_rule else key
    heads = headings(lines)
    spans = [] if as_rule else list(zip(heads, ends(heads, len(lines))))
    numbered = [s for s in spans if (m := NUMBERED.match(s[0][2])) and m.group(1) == key]
    for pick in (numbered, [s for s in spans if plain(s[0][2]) == plain(key) or s[0][2].lower() == key],
                 [s for s in spans if key in s[0][2].lower()]):
        if len(pick) == 1:
            (a, _, _), z = pick[0]
            return True, [f"{path}:{a}-{z} ({size(lines, a, z)} B)", *lines[a - 1:z]]
        if pick:
            starts = {s[0][0] for s in pick}
            return False, [f"{path}: '{ref}' names {len(pick)} sections:"] + [t for t in toc(path, text)[1:] if int(t[:5]) in starts]
    rule = next((n for n, line in enumerate(lines, 1) if re.match(rf"{re.escape(key)}\. ", line)), None) if NUMBERED.fullmatch(key) else None
    if rule:
        z = rule
        while z < len(lines) and lines[z].startswith((" ", "\t")) and lines[z].strip():
            z += 1
        return True, [f"{path}:{rule}-{z} (rule {key}.)", *lines[rule - 1:z]]
    return False, [f"{path}: no section '{ref}'; its headings:", *toc(path, text)[1:]]


@dataclass
class Hit:
    rank: int
    group: int
    path: str
    line: int
    text: str


def names(line, low):
    """A table row names the term: a cell that is the term, or starts with it in backticks."""
    s = line.strip()
    cells = [c.strip().strip("*").strip().lower() for c in s.strip("|").split("|")] if s.startswith("|") else []
    return any(c.strip("`") == low or c.startswith(f"`{low}`") for c in cells)


def defining(docs, term):
    """{(path, line)} of what defines a term: a BACKLOG/ROADMAP row that names it, its spec's first line."""
    low, out = term.lower(), set()
    for p in ROWS:
        if p in docs.paths:
            out |= {(p, n) for n, line in enumerate(docs.text(p).splitlines(), 1) if names(line, low)}
    return out | {(p, 1) for p in (f"{SPECS}{term}.md", f"{DONE}{term}.md") if p in docs.paths}


def one_line(text):
    return " ".join(text.split())


def cut(text, term, room):
    """One line in `room` chars: whole, else its start and the part around the term, each cut marked `…`."""
    t = one_line(text)
    if len(t) <= room:
        return t
    at = t.lower().find(term.lower())
    if at + len(term) < room - 1:
        return t[:room - 1] + "…"
    head = t[:room // 3]
    left = room - len(head) - 2
    start = max(len(head), at - left // 3)
    return head + "…" + t[start:start + left] + ("…" if start + left < len(t) else "")


def contexts(lines):
    """Each line's section, as its number (`3.2`) or its heading's first 24 chars; `#` titles and heading lines get none."""
    out, cur = {}, None
    heads = {n: (lv, t) for n, lv, t in headings(lines)}
    for n in range(1, len(lines) + 1):
        if n in heads:
            lv, t = heads[n]
            cur = None if lv == 1 else (m.group(1) if (m := re.match(r"(\d+(?:\.\d+)*)\.?\s", t)) else t[:24])
            out[n] = None
        else:
            out[n] = cur
    return out


def hits(docs, paths, term, defs):
    word, low = re.compile(rf"(?<![\w-]){re.escape(term)}(?![\w-])", re.I), term.lower()
    starts = re.compile(rf"^#{{1,6}} +(\d+(\.\d+)*\.? +)?{re.escape(term)}(?![\w-])", re.I)
    docs.load(paths)
    out = []
    for p in paths:
        lines, ctx = docs.text(p).splitlines(), None
        for n, line in enumerate(lines, 1):
            if (p, n) in defs or starts.match(line):
                rank = DEFINES
            elif low not in line.lower():
                continue
            else:
                rank = ID if f"`{low}`" in line.lower() else WORD if word.search(line) else PART
            ctx = ctx or contexts(lines)
            prefix = f"  :{n} " + (f"§{ctx[n]} " if ctx[n] else "")
            out.append(Hit(rank, group(p), p, n, prefix + cut(line, term, HIT_CHARS - len(prefix))))
    return out


def status(docs, term, defs):
    """`== term · BACKLOG: <its row> · spec <path>`, or None when nothing defines the term."""
    parts = [f"{Path(p).stem}: {cut(docs.text(p).splitlines()[n - 1], term, 140)}" for p, n in sorted(defs) if p in ROWS][:2]
    parts += [f"spec {p}" for p, _ in sorted(defs) if p.startswith(SPECS)]
    return " · ".join([f"== {term}", *parts]) if parts else None


def count(n):
    return f"{n} hit{'' if n == 1 else 's'}"


def report(term, found, budget, n_docs, head):
    """The lines of one term within budget (None: all of them)."""
    if not found:
        return [f"{head} · no hits" if head else f"== {term}: no hits in {n_docs} docs"]
    found.sort(key=lambda h: (h.rank, h.group, h.path, h.line))
    files = {}
    for h in found:
        files.setdefault(h.path, []).append(h)
    lines = [(head + " · " if head else f"== {term}: ") + f"{count(len(found))} in {len(files)} file{'' if len(files) == 1 else 's'}"]
    if budget is None or 1 + len(files) + len(found) <= budget:
        shown = {id(h) for h in found}
    else:
        shown, used, opened = set(), 1, set()
        for h in found:
            cost = 1 + (h.path not in opened)
            if used + cost > budget - 2:                     # two lines kept for `+ also in` and `+K more`
                break
            used, opened = used + cost, opened | {h.path}
            shown.add(id(h))
    also = []
    for path, hs in files.items():
        mine = [h for h in hs if id(h) in shown]
        if not mine:
            also.append(f"{path} {len(hs)}")
            continue
        lines.append(f"{path}: {count(len(hs))}" + (f", {len(mine)} shown" if len(mine) < len(hs) else ""))
        lines += [h.text for h in mine]
    if also:
        text, left = "+ also in: ", list(also)
        while left and len(text) + len(left[0]) < ALSO_CHARS:
            text += left.pop(0) + ", "
        lines.append(text.rstrip(", ") + (f", … +{len(left)} files (--all)" if left else ""))
    if len(shown) < len(found):
        lines.append(f"+{len(found) - len(shown)} more (--all)")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("terms", nargs="*", help="ids, decisions or words; case does not matter")
    ap.add_argument("--toc", action="append", default=[], metavar="DOC")
    ap.add_argument("--section", action="append", default=[], metavar="DOC#REF")
    ap.add_argument("--in", dest="within", nargs="+", metavar="DOC", help="search only these docs or folders")
    ap.add_argument("--rev", help="read this git revision instead of the worktree")
    ap.add_argument("--root", help="the repository (default: the one holding the current folder)")
    ap.add_argument("--all", action="store_true", help="no line budget")
    ap.add_argument("--max", type=int, default=MAX_LINES, help=f"the line budget (default {MAX_LINES})")
    a = ap.parse_args(argv)
    if not (a.terms or a.toc or a.section):
        ap.error("give a term, --toc or --section")
    sys.stdout.reconfigure(encoding="utf-8")
    top = git(Path.cwd(), "rev-parse", "--show-toplevel")
    docs = Docs(a.root or (top.stdout.decode().strip() if top.returncode == 0 else "."), a.rev)
    out, ok = [], True

    def one(name):
        found = docs.resolve(name)
        if len(found) != 1:
            raise SystemExit(f"{name}: {'no such doc' if not found else 'names ' + str(len(found)) + ' docs'}")
        return found[0]
    for name in a.toc:
        out += toc(one(name), docs.text(one(name)))
    for ref in a.section:
        name, _, part = ref.partition("#")
        got, rows = section(one(name), docs.text(one(name)), part)
        ok, out = ok and got, out + rows
    if a.terms:
        paths = docs.default()
        if a.within:
            picked = [docs.resolve(n) for n in a.within]
            if not all(picked):
                raise SystemExit("no such doc: " + ", ".join(n for n, p in zip(a.within, picked) if not p))
            paths = sorted({p for ps in picked for p in ps}, key=lambda p: (group(p), p))
        budget = None if a.all else max(MIN_PER_TERM, a.max // len(a.terms))
        for term in a.terms:
            defs = defining(docs, term)
            found = hits(docs, paths, term, defs)
            ok = ok and bool(found)
            out += report(term, found, budget, len(paths), status(docs, term, defs))
    print("\n".join(out))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
