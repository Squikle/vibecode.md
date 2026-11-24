"""Merge a finished batch branch into the checked-out main and do the bookkeeping.

Usage (from main's worktree): python -I -X utf8 tools/work/integrate.py <branch> <id> [<id> ...]

- First checks each id's report and its row: a missing one stops with nothing merged.
- git merge --no-ff --no-commit <branch>; on conflicts prints CONFLICTS LEFT and the files, exits 1.
  Resolve, `git add`, run again with the same arguments: it resumes.
- Per id: drops its row from docs/BACKLOG.md (renumbers), moves docs/specs/<id>.md to done/,
  adds the report's "Built so far" row (a line starting "| `<id>` |") to docs/HANDOVER.md section 1,
  appends the report (.work/<id>/report.md in the main checkout) to docs/reports/built.md.
- Numbers each `| NEW |` decision in docs/DECISIONS.md after the highest number.
- Commits the merge: "Merge <branch>: <ids>."
"""
import pathlib, re, subprocess, sys

branch, ids = sys.argv[1], sys.argv[2:]


def git(*a, check=True):
    r = subprocess.run(["git", *a], capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode:
        sys.exit(f"git {' '.join(a)} failed:\n{r.stderr}")
    return r


top = pathlib.Path(git("rev-parse", "--show-toplevel").stdout.strip())
common = pathlib.Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()).parent
gitdir = pathlib.Path(git("rev-parse", "--path-format=absolute", "--git-dir").stdout.strip())

# Each report and its row, checked before the merge starts: a row in backticks or a code fence matched nothing,
# and the batch merged with no HANDOVER row.
for i in ids:
    rep = common / ".work" / i / "report.md"
    if not rep.exists():
        sys.exit(f"NO REPORT {rep}: nothing merged")
    if not any(l.startswith(f"| `{i}` |") for l in rep.read_text(encoding="utf-8").splitlines()):
        sys.exit(f'NO HANDOVER ROW in {rep}: a line starting "| `{i}` |" (not in backticks or a fence); nothing merged')

if not (gitdir / "MERGE_HEAD").exists():
    r = git("merge", "--no-ff", "--no-commit", branch, check=False)
    if r.returncode and "CONFLICT" not in r.stdout + r.stderr:
        sys.exit(r.stdout + r.stderr)
left = git("diff", "--name-only", "--diff-filter=U").stdout.split()
if left:
    print("CONFLICTS LEFT:\n  " + "\n  ".join(left))
    sys.exit(1)


def rw(path, fn):
    p = top / path
    if not p.exists():
        return
    raw = p.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    new = fn(raw.split(nl), nl)
    p.write_bytes(nl.join(new).encode("utf-8"))


def drop_rows(lines, nl):
    out, n = [], 0
    for l in lines:
        m = re.match(r"^\| (\d+) \|", l)
        if m and any(f"`{i}`" in l.split("|")[2] for i in ids):
            continue
        if m:
            n += 1
            l = f"| {n} |" + l[m.end():]
        out.append(l)
    return out


rw("docs/BACKLOG.md", drop_rows)

built, reports = [], []
for i in ids:
    rep = common / ".work" / i / "report.md"
    if not rep.exists():
        print(f"WARN no report for {i} at {rep}")
        continue
    text = rep.read_text(encoding="utf-8")
    built += [l for l in text.splitlines() if l.startswith(f"| `{i}` |")][:1]
    s = text.find(f"## {i}")
    reports.append((text[s:] if s >= 0 else f"## {i}\n\n" + text).strip())
    spec = top / "docs/specs" / f"{i}.md"
    if spec.exists():
        (top / "docs/specs/done").mkdir(parents=True, exist_ok=True)
        git("mv", str(spec), str(top / "docs/specs/done" / f"{i}.md"))


def add_built(lines, nl):
    if not built:
        return lines
    try:
        h = next(k for k, l in enumerate(lines) if l.startswith("## 1."))
    except StopIteration:
        return lines
    end = h + 1
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    last = max((k for k in range(h, end) if lines[k].startswith("|")), default=end - 1)
    return lines[: last + 1] + built + lines[last + 1:]


rw("docs/HANDOVER.md", add_built)

if reports:
    rp = top / "docs/reports/built.md"
    rp.parent.mkdir(parents=True, exist_ok=True)
    old = rp.read_text(encoding="utf-8") if rp.exists() else "# Reports of the merged batches\n"
    rp.write_text(old.rstrip("\n") + "\n\n" + "\n\n".join(reports) + "\n", encoding="utf-8", newline="\n")


def number(lines, nl):
    top_n = max([int(m.group(1)) for l in lines if (m := re.match(r"^\| (\d+) \|", l))] or [0])
    out = []
    for l in lines:
        if l.startswith("| NEW |"):
            top_n += 1
            l = f"| {top_n} |" + l[len("| NEW |"):]
            print(f"decision {top_n}: {l[:80]}")
        out.append(l)
    return out


rw("docs/DECISIONS.md", number)
git("add", "-A")
git("commit", "-q", "-m", f"Merge {branch}: {', '.join(ids)}.")
print("merged:", git("log", "--oneline", "-1").stdout.strip())
