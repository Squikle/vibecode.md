"""Merge a finished batch branch into the checked-out main and do the bookkeeping.

Usage (from main's worktree): python -I -X utf8 tools/work/integrate.py <branch> <id> [<id> ...]

- First checks each id's report, its row and its cause (merge-cause: the branch's spec, or a `From:` line in the
  report naming a `ref` or the owner's dated word; tools/work/cause.py): a missing one stops with nothing merged.
- git merge --no-ff --no-commit <branch>; on conflicts prints CONFLICTS LEFT and the files, exits 1.
  Resolve, `git add`, run again with the same arguments: it resumes.
- Builds the doc vault of the merged docs in a temp folder (tools/vault/vaultify.py --src): a miss (a table row of the
  wrong shape) prints VAULT MISS lines and stops with nothing committed; fix, `git add`, run again: it resumes.
- Per id: drops its row from docs/BACKLOG.md (renumbers), moves docs/specs/<id>.md to done/,
  adds the report's "Built so far" row (a line starting "| `<id>` |") to docs/HANDOVER.md section 1,
  appends the report (.work/<id>/report.md in the main checkout) to docs/reports/built.md.
- Numbers each `| NEW |` decision in docs/DECISIONS.md after the highest number.
- Commits the merge: "Merge <branch>: <ids>."
"""
import argparse, json, pathlib, re, shutil, subprocess, sys, tempfile

# Parsed before anything runs: a `-h` once read as a branch name and the merge went on.
_ap = argparse.ArgumentParser(prog="integrate.py", description="Merge a finished batch branch into the checked-out "
                              "main and do the bookkeeping (BACKLOG, specs, HANDOVER, the report, DECISIONS).")
_ap.add_argument("branch", help="the batch branch to merge")
_ap.add_argument("ids", nargs="+", metavar="id", help="a batch id the branch completes (its .work/<id>/report.md)")
_args = _ap.parse_args()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # `python -I` leaves it off
from cause import HAS_CAUSE  # noqa: E402

branch, ids = _args.branch, _args.ids


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
    text = rep.read_text(encoding="utf-8")
    if not any(l.startswith(f"| `{i}` |") for l in text.splitlines()):
        sys.exit(f'NO HANDOVER ROW in {rep}: a line starting "| `{i}` |" (not in backticks or a fence); nothing merged')
    # Its cause: `gate.py ship` writes it to docs/MERGES.md, so a batch with none never merges.
    spec = any(git("cat-file", "-e", f"{branch}:docs/specs/{d}{i}.md", check=False).returncode == 0 for d in ("done/", ""))
    if not spec and not HAS_CAUSE.search(text):
        sys.exit(f'NO CAUSE for {i}: no docs/specs/{i}.md on {branch}, no "From:" line in {rep}; nothing merged')

if not (gitdir / "MERGE_HEAD").exists():
    r = git("merge", "--no-ff", "--no-commit", branch, check=False)
    if r.returncode and "CONFLICT" not in r.stdout + r.stderr:
        sys.exit(r.stdout + r.stderr)
left = git("diff", "--name-only", "--diff-filter=U").stdout.split()
if left:
    print("CONFLICTS LEFT:\n  " + "\n  ".join(left))
    sys.exit(1)


def vault_misses():
    """The vault built from the merged docs (this tree's `docs/` and `AGENTS.md`, copied) into a temp folder, never
    `.work/vault`: its misses, [] when clean. None with no vaultify.py (a project without the vault)."""
    tool = next((p for p in (top / "tools/vault/vaultify.py", pathlib.Path(__file__).resolve().parents[1] / "vault/vaultify.py")
                 if p.is_file()), None)
    if tool is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "tree"
        src.mkdir()
        if (top / "docs").is_dir():
            shutil.copytree(top / "docs", src / "docs")
        if (top / "AGENTS.md").is_file():
            shutil.copy(top / "AGENTS.md", src / "AGENTS.md")
        got = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(tool), "--src", str(src),
                              "--vault", str(pathlib.Path(tmp) / "out" / "vault")],
                             capture_output=True, text=True, encoding="utf-8")
    found = [l[len("miss: "):] for l in got.stderr.splitlines() if l.startswith("miss: ")]
    try:  # vaultify prints its first 8 misses only; its JSON line counts them all
        more = json.loads(got.stdout.strip().splitlines()[-1]).get("misses", 0) - len(found)
    except (ValueError, IndexError, AttributeError):
        more = 0
    if more > 0:
        found.append(f"and {more} more (vaultify prints its first 8)")
    if got.returncode and not found:
        found = ["the vault build failed: " + got.stderr.strip()[-300:]]
    return found


# The vault main's next build makes, built first from the merged docs: a table row of the wrong shape (a stray cell
# left where a row was dropped by hand) was found only after the merge. Two branches each clean can merge into a miss.
missed = vault_misses()
if missed:
    for m in missed:
        print("VAULT MISS: " + m, file=sys.stderr)
    sys.exit("the VAULT MISS lines above are in the merged docs; nothing committed. Fix each here (`git add`, run "
             "again: it resumes) or on the branch (`git merge --abort`, fix, run again); a miss main holds already "
             "(.work/vault-alert.json names it) is fixed on main first")
print("vault check: " + ("no tools/vault/vaultify.py, skipped" if missed is None else "clean"))


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
