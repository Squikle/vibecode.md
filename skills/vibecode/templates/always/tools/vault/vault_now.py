#!/usr/bin/env python3
"""The coordinator's running jobs -> `<vault>/Now.md`. Zero LLM, stdlib only (Windows and macOS).

python -I -X utf8 tools/vault/vault_now.py [--src <checkpoint>] [--vault <dir>] [--if-changed]   (always exit 0)

Defaults: `<main tree>/.work/coordinator-now.md` and `<main tree>/.work/vault` (the main tree by `git rev-parse
--git-common-dir`, as vaultify finds it). Meant for a no-model shell automation every 2 minutes, with `--if-changed`.

Reads the checkpoint's job tables (a header with a Batch column and a Next or State column; any other table is skipped),
tolerantly: columns by header name, an extra cell joins the state, a short row is "couldn't read row N" (its line), never
a crash. One row per job: the state (building, reviewing, gate, waiting, done: in that order), the role and what the
batch is for (its note's title), the batch linked to `batches/<id>` when that note exists, what it does now, since when,
and what comes next. Session and automation ids are dropped and the code names in the tag column read as "the <role>
(<batch>)" (the owner's naming rule: roles and batch ids, never code names). The `built` line is the checkpoint's mtime.

`--if-changed` exits at once when the checkpoint, this script, the vault's last build (`.vaultify/build.json`) and Now.md's
existence are as they were (`.vaultify/now.json`). Every run adds one line to `vault-now.log` next to the vault.
vaultify never deletes Now.md, keeps it out of its manifest, and embeds it in the hub (`![[Now]]`)."""
import argparse, hashlib, json, os, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
KINDS = ("building", "reviewing", "gate", "waiting", "done")
WORDS = (("done", r"\b(done|retired?|merged)\b"),
         ("reviewing", r"\breview(ing)?\b|\bchanges needed\b"),
         ("gate", r"\b(re)?gate\b(?!-)|\bstack \d|\bbisect"),
         ("waiting", r"\bwaits?\b|\bwaiting\b|\bqueued\b|\bblocked\b|\bcheck-back\b"))
GOAL = re.compile(r"\W*(READY|CULPRIT|SHIP|SYNCED|MERGED)\b")
CLOCK = re.compile(r"(?<![\w:])(?:[01]?\d|2[0-3]):[0-5]\d(?!\d|:\d)")
IDS = re.compile(r"`?\b(?:ses|aut)_\w+`?")
TAG = re.compile(r"^[A-Z][A-Z0-9-]*[A-Z0-9]$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
KEEP_LOG = 200


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def column(head, *names):
    for i, h in enumerate(head):
        if any(h.startswith(n) for n in names):
            return i
    return None


def tables(text):
    """(header cells, [(line number, cells)]) for every job table. A blank line inside one (rows go on after it, with
    no header of their own) does not end it."""
    lines, out, i = text.splitlines(), [], 0
    row = lambda j: j < len(lines) and lines[j].lstrip().startswith("|")
    head_at = lambda j: row(j) and j + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[j + 1]) and "-" in lines[j + 1]
    while i < len(lines) - 1:
        if head_at(i):
            head = [h.lower() for h in cells(lines[i])]
            body, i = [], i + 2
            while row(i) or (i < len(lines) and not lines[i].strip() and row(i + 1) and not head_at(i + 1)):
                if row(i):
                    body.append((i + 1, cells(lines[i])))
                i += 1
            if column(head, "batch") is not None and column(head, "next", "state") is not None:
                out.append((head, body))
        else:
            i += 1
    return out


def jobs(text):
    """Raw job dicts and the line numbers of the rows that could not be read."""
    out, bad = [], []
    for head, body in tables(text):
        at = {k: column(head, *n) for k, n in (("role", ("role", "job")), ("tag", ("my tag", "tag")),
                                               ("session", ("session",)), ("batch", ("batch",)),
                                               ("state", ("next", "state")))}
        for n, row in body:
            if len(row) < len(head) or not row[at["batch"]] and (at["role"] is None or not row[at["role"]]):
                bad.append(n)
                continue
            get = lambda k: row[at[k]] if at[k] is not None else ""
            extra = [c for c in row[len(head):] if c]
            out.append({"role": get("role") or "job", "tag": get("tag"), "session": get("session"),
                        "batch": get("batch"), "state": "; ".join([get("state"), *extra]).strip("; ")})
    return out, bad


def batch_of(cell):
    """(id or None, the cell's other words): the first `code` span, or the whole cell when it reads as an id."""
    m = re.search(r"`([^`]+)`", cell)
    if m:
        return m.group(1), (cell[:m.start()] + cell[m.end():]).strip()
    plain = cell.strip()
    return (plain, "") if SLUG.match(plain) else (None, plain)


def names(rows):
    """Code name -> "the <role> (<batch>)", from the tag column (times and `-` are not names)."""
    out = {}
    for r in rows:
        if TAG.match(r["tag"]):
            bid, _ = batch_of(r["batch"])
            out[r["tag"]] = f"the {r['role']} ({bid})" if bid else f"the {r['role']}"
    return out


def plain(s, code):
    s = IDS.sub("", s)
    for name in sorted(code, key=len, reverse=True):
        s = re.sub(rf"\b{re.escape(name)}\b", code[name], s)
    s = re.sub(r"\b(?:the same |same |the )?REV\b", "the reviewer", s)
    s = re.sub(r"\(\s*\)", "", re.sub(r"\(\s+", "(", re.sub(r"\s+([),;])", r"\1", s)))
    return re.sub(r"\s{2,}", " ", s).strip(" ;,").replace("|", "\\|")


def split(state):
    """(earlier steps, the current step, next). Steps part on arrows (or the double space a lost one leaves); `; then`
    starts what is next. The current step follows the last timed one (the chain is history -> now -> plan), unless
    that is a goal (READY, CULPRIT ...): then the timed step is still going. No times: the last step, or the one before
    a closing goal."""
    tail = ""
    m = re.search(r";\s*then\b\s*|\.\s+Then\b\s*", state)
    if m:
        state, tail = state[:m.start()], state[m.end():]
    parts = [p for p in re.split(r"\s*(?:→|->|\s{2,})\s*", state.strip()) if p.strip()] or [""]
    goal = lambda i: GOAL.match(parts[i].replace("`", ""))
    timed = [i for i, p in enumerate(parts) if CLOCK.search(p)]
    if timed:
        cur = timed[-1] + 1 if timed[-1] < len(parts) - 1 and not goal(timed[-1] + 1) else timed[-1]
    else:
        cur = len(parts) - 2 if len(parts) > 1 and goal(-1) else len(parts) - 1
    nxt = " → ".join(parts[cur + 1:])
    nxt = f"{nxt}; then {tail}" if nxt and tail else nxt or tail
    return parts[:cur], parts[cur], nxt

def kind(step):
    low = step.lower()
    return next((k for k, rx in WORDS if re.search(rx, low)), "building")


def title(vault, bid):
    p = vault / "batches" / f"{bid}.md"
    try:
        m = re.search(r'^title: "(.*)"$', p.read_text(encoding="utf-8").split("\n---\n", 1)[0], re.M)
    except (OSError, UnicodeError):
        return None
    return (m.group(1).replace('\\"', '"') if m else "")


def row(job, vault, code):
    before, now, nxt = split(job["state"])
    earlier = next((CLOCK.findall(p) for p in reversed(before) if CLOCK.search(p)), [])
    since = (CLOCK.findall(now) or earlier[-1:] or [""])[0]
    state = kind(now)
    if "automation" in job["session"].lower():
        at = CLOCK.findall(job["tag"])
        state, now = "waiting", f"an automation starts it{' at ' + at[0] if at else ''}: {now}"
    bid, words = batch_of(job["batch"])
    t = title(vault, bid) if bid else None
    what = job["role"] + (f": {t}" if t else "")
    if t is not None:                                       # the link's target is a batch id; its other words are text
        link = f"[[batches/{bid}\\|{bid}]]" + (f" {plain(words, code)}" if words else "")
    else:
        link = plain(f"{bid or job['batch'] or '-'}, no backlog row", code)
    cols = [state, plain(what, code), link, plain(now, code), since, plain(nxt, code)]
    return state, "| " + " | ".join(cols) + " |"


def render(text, mtime, vault):
    found, bad = jobs(text)
    code = names(found)
    built = [row(j, vault, code) for j in found]
    built.sort(key=lambda r: KINDS.index(r[0]))
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
    lines = ["---", "tags:", '  - "vault/now"', "---", "# Now",
             f"_built {stamp} from the coordinator's checkpoint · {len(built)} job{'s' if len(built) != 1 else ''}_", "",
             "| State | Job | Batch | Now | Since | Next |", "|---|---|---|---|---|---|", *(r for _, r in built)]
    if bad:
        lines += ["", *(f"- couldn't read row {n}" for n in bad)]
    return ("\n".join(lines) + "\n").encode("utf-8"), len(built), len(bad)


def log(vault, line):
    p = vault.parent / "vault-now.log"
    try:
        old = p.read_text(encoding="utf-8").splitlines()[-(KEEP_LOG - 1):] if p.is_file() else []
        p.write_bytes(("\n".join([*old, time.strftime("%Y-%m-%d %H:%M:%S ") + line]) + "\n").encode("utf-8"))
    except OSError:
        pass


def key(src, vault):
    h = hashlib.sha256()
    for p in (src, Path(__file__), vault / ".vaultify" / "build.json"):
        h.update(p.read_bytes() if p.is_file() else b"-")
        h.update(b"\0")
    h.update(b"now" if (vault / "Now.md").is_file() else b"none")
    return h.hexdigest()[:16]


def work_dir():
    """`<main tree>/.work`: the main tree is the parent of git's common dir (every worktree shares it)."""
    try:
        r = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, creationflags=NO_WINDOW)
        common = r.stdout.decode().strip() if not r.returncode else ""
    except OSError:
        common = ""
    return (Path(common).parent if common else HERE.parents[1]) / ".work"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", help="the coordinator's checkpoint (default: <main tree>/.work/coordinator-now.md)")
    ap.add_argument("--vault", help="default: <main tree>/.work/vault")
    ap.add_argument("--if-changed", action="store_true")
    a = ap.parse_args(argv)
    work = work_dir() if not (a.src and a.vault) else None
    t0 = time.perf_counter()
    src, vault = Path(a.src or work / "coordinator-now.md"), Path(a.vault or work / "vault")
    try:
        state, k = vault / ".vaultify" / "now.json", key(src, vault)
        if a.if_changed and state.is_file() and json.loads(state.read_bytes()).get("key") == k:
            log(vault, f"skip seconds={time.perf_counter() - t0:.3f}")
            return 0
        if not src.is_file():
            raise OSError(f"no checkpoint at {src}")
        data, n, bad = render(src.read_bytes().decode("utf-8", "replace"), src.stat().st_mtime, vault)
        out = vault / "Now.md"
        if not out.is_file() or out.read_bytes() != data:
            tmp = out.with_name("Now.md.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, out)
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_bytes((json.dumps({"key": key(src, vault)}) + "\n").encode())
        log(vault, f"built jobs={n} unread={bad} seconds={time.perf_counter() - t0:.3f}")
    except Exception as e:                                  # a scheduled job: one log line, never a failure
        log(vault, f"error {type(e).__name__}: {e}")
        print(f"vault_now: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
