#!/usr/bin/env python3
"""The pages a reviewer reads first: `python -I -X utf8 tools/work/review-pack.py <branch> [--base main]`.

It writes `<main tree>/.work/<id>/pack.md` (the batch's folder: `<branch>`, or the folder whose `report.md` names the
branch, or `--id`), and `pack-2.md`, ... when it needs more pages of `--cap` characters, and prints one line:
`PACK <branch> <head8> <chars> chars (~<tokens> tokens) in <n> pages | ... -> <path>`.
In it, in this order: the head's latest gate line and its reds (`.work/ci/<sha8>.json` or `.only.json`); the commits
and the diffstat against the merge base; the builder's report path and its What/Try parts; the spec's "What must be
true"; the docs/ROADMAP.md and docs/BACKLOG.md rows that name the batch; one `tools/work/map.py` row per touched source
file (its line count, its exported names, the declarations the diff touches, its job from docs/architecture); then each
code file's diff whole (`--big N`: a longer one as its hunks alone, `-U0`), over as many pages as it takes, and the
tests' and docs' diffs while the last page has room; each file left out named with its stat and its command.

Git and Python only (no model, no network, no shell). Unknown arguments are an error.

How a reviewer calls it (from the batch's worktree or the main tree):
    python -I -X utf8 tools/work/review-pack.py <branch>            # prints PACK ... in <n> pages -> <pack.md>
    then read pack.md, pack-2.md, ... whole, one read each (each page is under --cap, 40k characters);
    a file under "Not shown" only when a finding needs it: the `git diff` command on its line;
    the source around a hunk with `tools/work/map.py --find <name>` and a ranged read, never a whole file again.
    A commit, or a branch whose report is in another folder: add `--id <the .work folder>`; another base: `--base`."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE)]
import map as source_map  # noqa: E402  tools/work/map.py, read only as a module

CAP = 40_000  # characters a page (about 10k tokens): one read each
BIG = 0  # with --big N, a diff longer than N lines shows its hunks alone (-U0); by default each code diff is whole
TESTS = 2  # `order`'s tier from which a diff goes in only where it fits (the tests, then the data and docs)
LINE = 1_000  # a longer diff line is cut
ROW_CHARS = 400
REPORT_CHARS = 4_000
SPEC_CHARS = 3_000
COMMITS = 40
NO_DIFF = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock", "poetry.lock", "Cargo.lock", "go.sum",
           "composer.lock", "Gemfile.lock"}
CODE = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs", ".go", ".java", ".kt", ".cs", ".rb", ".swift",
        ".c", ".cc", ".cpp", ".h", ".hpp", ".php", ".vue", ".svelte")
HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", re.M)


class Fail(Exception):
    pass


def git(repo: Path, *args: str, ok_missing: bool = False) -> str:
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        if ok_missing:
            return ""
        raise Fail(f"git {' '.join(args)}: {((done.stderr or done.stdout).strip().splitlines() or ['failed'])[0]}")
    return done.stdout


def default_base(repo: Path) -> str:
    """The gate's base rule (`tools/ci/gate.py` `default_base`): local `main` (merges land there, pushed late), else
    `origin/main`."""
    done = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "refs/heads/main"], cwd=repo, capture_output=True)
    return "main" if done.returncode == 0 else "origin/main"


def main_tree(repo: Path) -> Path:
    """The main checkout: the folder that holds the shared `.git` (its `.work/` holds the batches' folders)."""
    return Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()).parent


def new_ranges(diff: str) -> list[tuple[int, int]]:
    """The head's line ranges a diff's hunks write (a pure removal: the line after it)."""
    out = []
    for m in HUNK.finditer(diff):
        start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
        out.append((max(start, 1), max(start, 1) + max(count, 1) - 1))
    return out


def touched(outline: list[tuple[int, int, str, bool]], ranges: list[tuple[int, int]], count: int) -> list[str]:
    """The declarations (`name`, a method as `Class.name`) the changed lines fall in; `(module)` for lines in none."""
    spans, owner = [], ""
    for i, (line, depth, what, _) in enumerate(outline):
        end = next((later[0] - 1 for later in outline[i + 1:] if later[1] <= depth), count)
        name = what.split()[-1]
        owner = name if depth == 0 else owner
        spans.append((line, end, depth, name if depth == 0 else f"{owner}.{name}"))
    names = []
    for first, last in ranges:
        for n in range(first, last + 1):
            inside = [s for s in spans if s[0] <= n <= s[1]]
            name = max(inside, key=lambda s: s[2])[3] if inside else "(module)"
            if name not in names:
                names.append(name)
    return names


def find_folder(repo: Path, work: Path, branch: str, wanted: str | None) -> Path:
    """The batch's `.work` folder: `--id`, the branch's own, else the one whose report names `branch` up top: one that
    calls it its own (``branch `X` ``, a `## X` line) before one that only mentions `` `X` ``."""
    if wanted:
        if wanted in (".", "..") or Path(wanted).name != wanted or "\\" in wanted:
            raise Fail(f"--id `{wanted}` is not one .work folder name")
        return work / wanted
    if (work / branch / "report.md").exists() or not work.is_dir():
        return work / branch
    own = re.compile(rf"branch `{re.escape(branch)}`|^#+ `?{re.escape(branch)}`?\s*$", re.M | re.I)
    named = []
    for report in sorted(work.glob("*/report.md")):
        head = "\n".join(report.read_text(encoding="utf-8", errors="replace").splitlines()[:5])
        if own.search(head):
            return report.parent
        if f"`{branch}`" in head:
            named.append(report.parent)
    if named:
        return named[0]
    if not git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", ok_missing=True).strip():
        raise Fail(f"`{branch}` is not a branch and no report names it: say which folder with --id <batch>")
    return work / branch


def report_parts(text: str) -> str:
    """The report's What and Try parts (a `## What changed` heading or a `**What changed**` line), else its text
    after the title."""
    blocks = re.split(r"(?m)^(?=#{2,4} |\*\*[A-Z])", text)
    keep = [b.strip() for b in blocks if re.match(r"(#{2,4} |\*\*)(what|try)", b, re.I)]
    body = "\n\n".join(keep) if keep else "\n".join(text.strip().splitlines()[1:]).strip()
    return cut(body, REPORT_CHARS)


def cut(text: str, chars: int) -> str:
    return text if len(text) <= chars else text[: chars - 30].rstrip() + "\n... (cut)"


def gate_lines(work: Path, head: str) -> list[str]:
    """The head's latest gate: its status line (and file), the lines under it, each red with its reason."""
    s8 = head[:8]
    found = [p for p in (work / "ci" / f"{s8}.json", work / "ci" / f"{s8}.only.json") if p.exists()]
    if not found:
        return [f"no gate result for {s8}: `python tools/ci/gate.py run {s8}`"]
    path = max(found, key=lambda p: p.stat().st_mtime)
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        lines = [result.get("line") or f"GATE {s8} {result['verdict']}", f"({path})"]
    except (ValueError, KeyError, TypeError) as e:
        return [f"gate result {path} unreadable: {e}"]
    for part in result.get("parts", []):
        where = f"/{part['machine']}" if part.get("machine") else ""
        if part.get("error"):
            lines.append(f"  ERROR {part['name']}{where}: {cut(str(part['error']), 160)} (log {part.get('log', '?')})")
        for red in part.get("reds", []):
            bits = [red.get("id", "?"), red.get("why", "new"), red.get("alone", "")]
            error = f": {cut(str(red['error']).splitlines()[0], 120)}" if red.get("error") else ""
            lines.append(f"  red {part['name']}{where} {' '.join(b for b in bits if b)}{error}")
    return lines[:40]


def rows(repo: Path, head: str, ids: list[str]) -> list[str]:
    marks = [f"`{i}`" for i in ids]
    out = []
    for doc in ("docs/ROADMAP.md", "docs/BACKLOG.md"):
        for n, line in enumerate(git(repo, "show", f"{head}:{doc}", ok_missing=True).splitlines(), 1):
            if line.startswith("|") and any(m in line for m in marks):
                out.append(f"{doc}:{n}: {cut(line, ROW_CHARS)}")
    return out


def spec_lines(repo: Path, head: str, ids: list[str]) -> list[str]:
    for i in ids:
        for doc in (f"docs/specs/{i}.md", f"docs/specs/done/{i}.md"):
            text = git(repo, "show", f"{head}:{doc}", ok_missing=True)
            if text:
                m = re.search(r"(?ms)^(#{2,4}) [^\n]*What must be true[^\n]*\n(.*?)(?=^#{1,4} |\Z)", text)
                return [doc, cut(m.group(2).strip(), SPEC_CHARS)] if m else [f"{doc} (no \"What must be true\")"]
    return []


def jobs(repo: Path, head: str) -> dict[str, str]:
    """Each file's job from the docs/architecture map rows (`| area | `path` | job |`) at the head."""
    out = {}
    names = git(repo, "ls-tree", "--name-only", head, "docs/architecture/", ok_missing=True).split()
    for doc in names:
        for line in git(repo, "show", f"{head}:{doc}", ok_missing=True).splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if line.startswith("|") and len(cells) >= 3 and re.fullmatch(r"`[^`]+`", cells[1]):
                out.setdefault(cells[1].strip("`"), cut(cells[2], 200))
    return out


def map_row(repo: Path, head: str, path: str, diff0: str, job: str | None) -> str | None:
    if Path(path).suffix not in source_map.SOURCES:
        return f"{path} | job: {job}" if job else None
    text = git(repo, "show", f"{head}:{path}", ok_missing=True)
    try:
        outline = source_map.py_outline(text) if path.endswith(".py") else source_map.ts_outline(text)
    except SyntaxError:
        outline = []
    count = len(text.splitlines())
    names = [w.split()[-1] for _, depth, w, exported in outline if exported and depth == 0]
    row = f"{path} {count}: {', '.join(names[:25]) or '(no exports)'}{' ...' if len(names) > 25 else ''}"
    hit = touched(outline, new_ranges(diff0), count)
    row += f" | touched: {', '.join(hit[:15])}{' ...' if len(hit) > 15 else ''}" if hit else ""
    return row + (f" | job: {job}" if job else "")


def order(path: str) -> tuple[int, str]:
    """The cap's order: the code, then what builds and runs it (compose, Dockerfiles, scripts), the tests, the data and
    docs; by path in each."""
    name = Path(path).name
    test = "/tests/" in f"/{path}" or "/test/" in f"/{path}" or name.startswith("test_") or ".test." in name \
        or ".spec." in name or name.endswith(("_test.go", "_test.py", "Test.java"))
    if path.endswith((".md", ".json", ".csv", ".txt")):
        return 3, path
    return (2 if test else 0 if name.endswith(CODE) else 1), path


def body(diff: str) -> str:
    """A file's diff without git's header lines (the pack names the file)."""
    lines = diff.splitlines()
    first = next((i for i, l in enumerate(lines) if l.startswith("@@")), len(lines))
    return "\n".join(lines[first:])


def dirty(repo: Path, branch: str) -> str | None:
    listing = git(repo, "worktree", "list", "--porcelain", ok_missing=True)
    for block in listing.split("\n\n"):
        if f"branch refs/heads/{branch}" in block:
            tree = block.splitlines()[0].split(" ", 1)[1]
            n = len(git(Path(tree), "status", "--porcelain", ok_missing=True).splitlines())
            return f"{n} uncommitted files in {tree} (not in this pack: `git -C {tree} diff`)" if n else None
    return None


def build(repo: Path, work: Path, branch: str, base: str, wanted: str | None, batches: list[str], cap: int,
          big: int) -> tuple[Path, list[str], dict]:
    head = git(repo, "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}", ok_missing=True).strip()
    if not head:
        raise Fail(f"no branch or commit `{branch}`")
    since = git(repo, "merge-base", base, head).strip()
    span, three = f"{since}..{head}", f"{base}...{branch}"
    folder = find_folder(repo, work, branch, wanted)
    ids = list(dict.fromkeys([folder.name, branch, *batches]))
    log = git(repo, "log", "--no-merges", f"--max-count={COMMITS}", "--format=%h %s", span).splitlines()
    numstat = [l.split("\t") for l in git(repo, "diff", "--no-renames", "--numstat", since, head).splitlines() if l]
    files = sorted((p for _, _, p in numstat), key=order)
    counts = {p: (a, r) for a, r, p in numstat}
    report = folder / "report.md"
    job = jobs(repo, head)
    zero = {p: git(repo, "diff", "--no-renames", "-U0", since, head, "--", p) for p in files}

    head_part = [f"# Review pack: `{branch}` {head[:8]} on {base} (merge base {since[:8]})",
                 "Made by `tools/work/review-pack.py` (git only); read this first, then only the lines it points at.",
                 "", "## Gate", *gate_lines(work, head), "",
                 f"## Commits ({len(log)}{'+' if len(log) == COMMITS else ''}, `git log {base}..{branch}`)", *log, "",
                 "## Diffstat", git(repo, "diff", "--no-renames", "--stat=120", since, head).rstrip()]
    if note := dirty(repo, branch):
        head_part.append(note)
    head_part += ["", f"## Builder report: {report}",
                  report_parts(report.read_text(encoding="utf-8", errors="replace")) if report.exists() else "(none)"]
    if spec := spec_lines(repo, head, ids):
        head_part += ["", f"## Spec: {spec[0]}", *spec[1:]]
    head_part += ["", f"## Rows naming {', '.join(f'`{i}`' for i in ids)}", *(rows(repo, head, ids) or ["(none)"])]
    maps = [r for p in files if (r := map_row(repo, head, p, zero[p], job.get(p)))]
    head_part += ["", "## Touched files (map)", *(maps or ["(no source file)"]), "", "## Diff"]
    head_part.insert(1, f"{PAGES}: read each, in order; the code is in whole, the tests and docs while there is room.")

    pages = Pages(cap, f"Review pack: `{branch}` {head[:8]}")
    pages.lines(head_part)

    def stat(p: str) -> str:
        return f"- `{p}` +{counts[p][0]} -{counts[p][1]}: `git diff {three} -- {p}`"

    def skip(p: str) -> bool:
        return Path(p).name in NO_DIFF or counts[p][0] == "-"

    # The stat lines a test or doc might leave: kept free on the page, so the list never needs a page of its own.
    pending = len(NOT_SHOWN) + 2 + sum(len(stat(p)) + 1 for p in files if skip(p) or order(p)[0] >= TESTS)
    shown, left = [], []
    for p in files:
        a, r = counts[p]
        if skip(p):
            left.append(p)
            continue
        lines, how = body(git(repo, "diff", "--no-renames", "-U3", since, head, "--", p)).splitlines(), ""
        if big and len(lines) > big:
            lines, how = body(zero[p]).splitlines(), ", hunks only"
        whole = order(p)[0] >= TESTS
        if pages.diff(f"### {p} (+{a} -{r}{how})", lines, whole=whole, reserve=pending - len(stat(p)) - 1):
            shown.append(p)
            pending -= (len(stat(p)) + 1) if whole else 0
        else:
            left.append(p)
    if left:
        pages.lines(["", NOT_SHOWN, *(stat(p) for p in left)])
    return folder, pages.texts(), {"head": head, "files": len(files), "shown": len(shown), "left": len(left)}


PAGES = "\x00pages\x00"  # filled in once the pages are counted
NOT_SHOWN = "## Not shown (tests and docs past the room, lock files, binaries)"


class Pages:
    """The pack in pages of at most `cap` characters: `pack.md`, `pack-2.md`, ...; a diff runs on over a page's end."""

    def __init__(self, cap: int, title: str):
        self.cap, self.title, self.pages, self.size = cap - 120, title, [[]], [0]  # 120: room for the filled names

    def room(self) -> int:
        return self.cap - self.size[-1]

    def put(self, line: str) -> None:
        line = line if len(line) <= LINE else line[: LINE - 10] + " ... (cut)"
        self.pages[-1].append(line)
        self.size[-1] += len(line) + 1

    def turn(self) -> None:
        self.pages.append([])
        self.size.append(0)
        self.put(f"# {self.title}, page {len(self.pages)} of {PAGES} (after the one before it)")

    def lines(self, parts: list[str]) -> None:
        """Each part's lines (a diffstat or a report's part is many: only a single line is cut at `LINE`)."""
        for line in (line for part in parts for line in part.split("\n")):
            if len(line) + 1 > self.room():
                self.turn()
            self.put(line)

    def diff(self, head: str, lines: list[str], whole: bool = False, reserve: int = 0) -> bool:
        """One file's diff; `whole` (the tests and docs) only where it fits in the page's room with `reserve` to
        spare, else False."""
        need = len(head) + 20 + sum(min(len(l), LINE) + 1 for l in lines)
        if whole and need + reserve > self.room():
            return False
        if need > self.room() and self.room() < len(head) + 400:  # a long one starts on a new page, not on a scrap
            self.turn()
        for line in ["", head, "````diff"]:
            self.put(line)
        for line in lines:
            if min(len(line), LINE) + 8 > self.room():
                self.put("````")
                self.turn()
                for more in ["", f"{head} (continued)", "````diff"]:
                    self.put(more)
            self.put(line)
        self.put("````")
        return True

    def texts(self) -> list[str]:
        names = f"{len(self.pages)} page{'s' if len(self.pages) > 1 else ''}" + (
            f" (`pack.md`, then `pack-2.md` .. `pack-{len(self.pages)}.md` beside it)" if len(self.pages) > 1 else "")
        return ["\n".join(p).replace(PAGES, names if i == 0 else str(len(self.pages))) + "\n"
                for i, p in enumerate(self.pages)]


def page_name(n: int) -> str:
    return "pack.md" if n == 1 else f"pack-{n}.md"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="review-pack.py", description=__doc__.strip().splitlines()[0])
    p.add_argument("branch")
    p.add_argument("--base", help="what the branch is measured against (default: local main, else origin/main)")
    p.add_argument("--id", help="the batch's .work folder name, when it is not the branch and no report names it")
    p.add_argument("--batch", nargs="*", default=[], help="more batch ids whose ROADMAP/BACKLOG rows to show")
    p.add_argument("--cap", type=int, default=CAP, help=f"a page's size in characters (default {CAP})")
    p.add_argument("--big", type=int, default=BIG, help="a diff over this many lines shows its hunks alone (default: off)")
    p.add_argument("--repo", type=Path, default=REPO, help=argparse.SUPPRESS)
    p.add_argument("--work", type=Path, help="the .work folder (default: the main tree's)")
    a = p.parse_args(argv)
    try:
        work = a.work or main_tree(a.repo) / ".work"
        folder, texts, info = build(a.repo, work, a.branch, a.base or default_base(a.repo), a.id, a.batch, a.cap, a.big)
    except (Fail, RuntimeError) as e:
        print(f"PACK {a.branch} FAIL {e}", file=sys.stderr)
        return 1
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("pack-*.md"):  # a longer pack's pages from before, and only those
        if re.fullmatch(r"pack-\d+\.md", old.name):
            old.unlink()
    for n, text in enumerate(texts, 1):
        (folder / page_name(n)).write_text(text, encoding="utf-8", newline="\n")
    chars = sum(len(t) for t in texts)
    print(f"PACK {a.branch} {info['head'][:8]} {chars} chars (~{chars // 4} tokens) in {len(texts)} "
          f"page{'s' if len(texts) > 1 else ''} | {info['files']} files, {info['shown']} diffs shown, "
          f"{info['left']} not shown -> {folder / 'pack.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
