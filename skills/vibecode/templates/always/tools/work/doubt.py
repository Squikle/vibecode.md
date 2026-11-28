"""Where code makes sessions doubt it: a code file a session read and then looked up the reason for (a doc, the
decisions, `git blame`), or named in a `doubt:` line. A comment or an odd branch is evidence, not a requirement: a
lane off its path neither changes it nor reads the library over it; it leaves one line. This collects that doubt for
free, across sessions, so a spot many sessions doubt gets one patch-or-rework look. The coordinator runs it with
`friction.py` every 4 hours (docs/workflow/coordinator-more.md#10).

Usage: python -I -X utf8 tools/work/doubt.py [--repo .] [--db <opencode.db>] [--since 48h] [--all] [--roles]
                     [--lane lane-brief] [--promote 3] [--state <file>] [--top 15] [<session id or export .json> ...]
  Sessions are read as `friction.py` reads them: the opencode database read-only (subagents only, `--all`: every
  session), or named session ids and exports; a directory as a source is refused. Each path is matched to a file
  `--repo` tracks (`git ls-files`; default the git root of the current folder; no git: its files) by the longest
  tail, so a worktree's or a clone's path counts for the same file and another project's file for none.
  Only lanes count: a session whose user text, reads or shell commands match `--lane` (default: it reads or is
  told about lane-brief). An integrator, a product lead or a reviewer reads code and docs as its job; `--roles` counts
  every session anyway.

Two signals per code file:
- **doubt lines:** `doubt: <file>[:<line>[-<line>]] | <why>` at a line start of an assistant reply.
- **lookups:** a session reads a code file (the read tool, a grep on it, a shell read: `Get-Content`,
  `Select-String`, `git grep .. -- <file>`) and within the next WINDOW tool calls looks up why it is so: a reason
  doc (a .md, .rst or .txt whose path says decision, design, architect, spec, adr or rfc, outside `docs/workflow/`
  and `docs/plans/`; a grep over such a folder) or `git blame` / `git log -L` on that file. A backlog, a handover
  or a grep over all of `docs/` is state or a name search, not a reason. One lookup per read, counted for the code
  file read last before it.
A lane's own task is no doubt: a lookup is dropped (and counted in the header) when the file read last is one the
session edits or writes (its diff) or one its brief names (any user message: the path, or a file name unique in the
repo), or when the doc looked up is one the brief names. A doubt line counts even on the lane's own file.

A file seen in `--promote` sessions or more is PROMOTE: a patch-or-rework look by the architect, never straight to a lane.
`--state <file>` remembers promoted files (JSON); each alerts once, as NEW. Exit 1 when a NEW promote is in the
report (no `--state`: any promote), else 0. It prints a markdown report and writes nothing but `--state`.
"""
import argparse, collections, importlib.util, json, pathlib, re, subprocess, sys, time

_spec = importlib.util.spec_from_file_location("friction", pathlib.Path(__file__).resolve().with_name("friction.py"))
friction = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(friction)

WINDOW = 5  # tool calls after a code read in which a lookup is still about it
CODE = {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".go", ".rs", ".java", ".kt",
        ".cs", ".c", ".h", ".cc", ".cpp", ".hpp", ".rb", ".php", ".swift", ".scala", ".lua", ".sh", ".ps1", ".psm1",
        ".sql", ".css", ".scss", ".html"}
DOC = {".md", ".mdx", ".rst", ".txt", ".adoc"}
# A doc that says why code is so; a backlog, a handover, questions or measurements are state, not a reason.
REASON = re.compile(r"decision|design|architect|spec|adr|rfc", re.I)
NOT_DOC = re.compile(r"(^|/)docs/(workflow|plans)/")  # how to work, a lane's own plan: not why the code is so
DOUBT = re.compile(r"(?im)^[\s>*_-]*doubt:\s*`?([^\s|`]+)`?\s*\|\s*(.+?)\s*[`*_]*\s*$")
SEGMENT = re.compile(r"[;\n|]|&&")
BLAME = re.compile(r"\bgit\b(\s+-C\s+\S+)?\s+(blame|annotate)\b|\bgit\b(\s+-C\s+\S+)?\s+log\b.*\s-L\s*\S")
WRITES = re.compile(r"\b(Set-Content|Add-Content|Out-File|New-Item|Remove-Item|Copy-Item|Move-Item|rm|cp|mv|tee)\b|"
                    r"\bgit\b.*\s(commit|add|mv|rm|checkout|switch|apply|merge|rebase|stash|restore)\b|(^|\s)>{1,2}\s*\S")
SPAN = re.compile(r"-L\s*(\d+)(?:,(\+?)(\d+))?")


class Repo:
    """The tracked files of one repo; any path names the file whose tail it ends in (case-blind), or none."""
    def __init__(self, root):
        root = pathlib.Path(root)
        done = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True)
        if done.returncode == 0 and done.stdout:
            files = done.stdout.decode("utf-8", "replace").split("\0")
        else:
            files = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and
                     not any(s.startswith(".") or s == "node_modules" for s in p.relative_to(root).parts)]
        self.files = {f.lower(): f for f in files if f}
        self.names, self.dirs, dirnames = collections.defaultdict(list), {}, collections.defaultdict(list)
        for low, f in self.files.items():
            self.names[low.rsplit("/", 1)[-1]].append(low)
            parts = f.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                d = "/".join(parts[:i])
                if d.lower() not in self.dirs:
                    self.dirs[d.lower()] = d
                    dirnames[parts[i - 1].lower()].append(d.lower())
        self.dirnames = dirnames

    @staticmethod
    def clean(raw):
        p = str(raw or "").strip().strip("'\"`,").replace("\\", "/")
        p = re.sub(r"#.*$", "", p)
        m = re.fullmatch(r"(.+?)(?::\d+(?:[:-]\d+)?)?", p)
        p = re.sub(r"^\./", "", m.group(1) if m else p).rstrip("/")
        return p.lower()

    def _match(self, p, cands, real):
        if p in real:
            return real[p]
        if "/" not in p:
            return real[cands[0]] if len(cands) == 1 else None
        longer = [c for c in cands if p.endswith("/" + c)]  # an absolute or worktree path: its longest repo tail
        if longer:
            return real[max(longer, key=len)]
        shorter = [c for c in cands if c.endswith("/" + p)]  # a path relative to a subfolder
        return real[shorter[0]] if len(shorter) == 1 else None

    def file(self, raw):
        p = self.clean(raw)
        return self._match(p, self.names.get(p.rsplit("/", 1)[-1], []), self.files) if p else None

    def dir(self, raw):
        p = self.clean(raw)
        return self._match(p, self.dirnames.get(p.rsplit("/", 1)[-1], []), self.dirs) if p else None


def is_doc(f):
    low = f.lower()
    return pathlib.PurePosixPath(low).suffix in DOC and not NOT_DOC.search(low) and bool(REASON.search(low))


def is_code(f):
    return pathlib.PurePosixPath(f.lower()).suffix in CODE


def is_doc_dir(d):  # `docs/specs`, `docs/architecture`; a grep over all of `docs/` is a search for a name
    low = d.lower() + "/"
    return bool(REASON.search(low)) and not NOT_DOC.search(low)


def span(a, b):
    return f"L{a}" if a == b else f"L{a}-{b}"


class Session:
    def __init__(self):
        self.calls, self.events, self.own, self.brief, self.doubts, self.lane = 0, [], set(), [], [], False


class Scan:
    """Takes the parts `friction.read_db` / `read_export` hand over; `finish()` counts them per file."""
    def __init__(self, repo, lane=r"lane-brief", roles=False):
        self.repo, self.by_sid, self.lane, self.roles = repo, {}, re.compile(lane, re.I), roles
        self.sessions = set()

    def part(self, sid, p, assistant=True, t=None):
        self.sessions.add(sid)
        s = self.by_sid.setdefault(sid, Session())
        if p.get("type") == "text":
            if p.get("synthetic"):
                return
            txt = p.get("text") or ""
            if assistant:
                s.doubts += [(m.group(1), m.group(2)) for m in DOUBT.finditer(txt)]
            else:
                s.brief.append(txt)
                s.lane = s.lane or bool(self.lane.search(txt))
            return
        if p.get("type") != "tool":
            return
        st = p.get("state") if isinstance(p.get("state"), dict) else {}
        inp = st.get("input") if isinstance(st.get("input"), dict) else {}
        tool = p.get("name") or p.get("tool") or "?"
        s.calls += 1
        i = s.calls
        if st.get("status") == "error":
            return
        path = inp.get("path") or inp.get("filePath") or inp.get("file_path")
        if tool in ("read", "shell", "bash"):  # a lane reads its brief, or was told to
            s.lane = s.lane or bool(self.lane.search(str(path or inp.get("command") or "")))
        if tool in ("edit", "write", "multiedit", "patch"):
            f = self.repo.file(path)
            if f:
                s.own.add(f)
        elif tool == "read":
            f = self.repo.file(path)
            if f and is_doc(f):
                s.events.append((i, "doc", f, None))
            elif f and is_code(f):
                off, lim = inp.get("offset"), inp.get("limit")
                rng = span(int(off), int(off) + int(lim) - 1) if isinstance(off, int) and isinstance(lim, int) else None
                s.events.append((i, "read", f, rng))
        elif tool == "grep":
            f = self.repo.file(path) if path else None
            d = None if f else self.repo.dir(path) if path else None
            if f and is_doc(f) or d and is_doc_dir(d):
                s.events.append((i, "doc", f or d + "/", None))
            elif f and is_code(f):
                s.events.append((i, "read", f, None))
        elif tool in ("shell", "bash"):
            self.shell(s, i, str(inp.get("command") or ""))

    def shell(self, s, i, cmd):
        for seg in SEGMENT.split(cmd):
            if not seg.strip() or WRITES.search(seg):
                continue
            words = [w for w in re.split(r"[\s'\"`()=]+", seg) if w]
            words = [re.sub(r"^(?:-L)?(?:\d+,\+?\d+|:[^:]+):", "", w) for w in words]  # `-L 5,9:<file>`
            files = [f for f in (self.repo.file(w) for w in words if re.search(r"[\\/.]", w)) if f]
            if BLAME.search(seg):
                m = SPAN.search(seg)
                rng = None
                if m:
                    a = int(m.group(1))
                    b = (a + int(m.group(3)) if m.group(2) else int(m.group(3))) if m.group(3) else a
                    rng = span(a, b)
                s.events += [(i, "blame", f, rng) for f in files if is_code(f)]
                continue
            docs = [f for f in files if is_doc(f)]
            docs += [d + "/" for d in (self.repo.dir(w) for w in words if REASON.search(w)) if d and is_doc_dir(d)]
            if friction.READS.search(seg):
                s.events += [(i, "read", f, None) for f in files if is_code(f) and not is_doc(f)]
            if docs:
                s.events.append((i, "doc", docs[0], None))

    def finish(self):
        """Per file: sessions, lookups, doubt lines, line ranges, one why; plus the reads and the dropped."""
        self.reads = self.dropped = self.lookups = self.doubt_lines = self.lanes = 0
        rows = {}
        def row(f):
            return rows.setdefault(f, {"file": f, "sessions": set(), "lookups": 0, "doubts": 0,
                                       "ranges": collections.Counter(), "why": "", "how": ""})
        for sid, s in self.by_sid.items():
            self.lanes += s.lane
            if not (s.lane or self.roles):  # an integrator, a product lead, a reviewer reads code and docs as its job
                continue
            brief = "\n".join(s.brief).replace("\\", "/").lower()
            def named(f):
                if f.endswith("/"):  # a grep over a docs folder: no brief names a whole folder as its task
                    return False
                low = f.lower()
                name = low.rsplit("/", 1)[-1]
                return low in brief or (len(self.repo.names.get(name, [])) == 1 and
                                        re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", brief) is not None)
            own = lambda f: f in s.own or named(f)
            last = None  # [call, file, lines, counted]
            for i, kind, f, rng in s.events:
                if kind == "read":
                    self.reads += 1
                    last = [i, f, rng, False]
                    continue
                if kind == "blame":
                    same = last is not None and last[1] == f and i - last[0] <= WINDOW
                    if same and last[3]:
                        continue
                    if same:
                        last[3] = True
                    target, rng, how = f, rng or (last[2] if same else None), "git blame"
                else:
                    if last is None or last[3] or i - last[0] > WINDOW:
                        continue
                    last[3] = True
                    target, rng, how = last[1], last[2], f"then {f}"
                    if named(f):
                        self.dropped += 1
                        continue
                if own(target):
                    self.dropped += 1
                    continue
                r = row(target)
                self.lookups += 1
                r["lookups"] += 1
                r["sessions"].add(sid)
                r["how"] = r["how"] or how
                if rng:
                    r["ranges"][rng] += 1
            for where, why in s.doubts:
                m = re.fullmatch(r"(.+?)(?::(\d+)(?:-(\d+))?)?", where)
                f = self.repo.file(m.group(1)) or m.group(1).replace("\\", "/")
                r = row(f)
                self.doubt_lines += 1
                r["doubts"] += 1
                r["sessions"].add(sid)
                r["why"] = r["why"] or friction.one_line(why, 100)
                if m.group(2):
                    r["ranges"][span(m.group(2), m.group(3) or m.group(2))] += 1
        return rows


def promote(rows, n, state):
    new, done = [], state.setdefault("promoted", {})
    for r in rows.values():
        r["promote"] = len(r["sessions"]) >= n
        r["new"] = r["promote"] and r["file"] not in done
        if r["new"]:
            new.append(r["file"])
            done[r["file"]] = {"first": time.strftime("%Y-%m-%dT%H:%M"), "sessions": len(r["sessions"])}
    return new


def report(scan, rows, label, top):
    cell = friction.cell
    who = "every role" if scan.roles else f"{scan.lanes} lanes"
    lines = [f"# Doubt: {len(scan.sessions)} sessions ({label}; {who}), {scan.reads} code reads, {scan.lookups} lookups "
             f"({scan.dropped} own-task dropped), {scan.doubt_lines} doubt lines, on {len(rows)} files", "",
             "| sessions | lookups | doubts | flag | file | lines | example |", "|---|---|---|---|---|---|---|"]
    ranked = sorted(rows.values(), key=lambda r: (-len(r["sessions"]), -(r["doubts"] + r["lookups"]), r["file"]))
    for r in ranked[:top]:
        flag = ("PROMOTE NEW" if r["new"] else "PROMOTE") if r["promote"] else ""
        where = ", ".join(k for k, _ in r["ranges"].most_common(3))
        lines.append(f"| {len(r['sessions'])} | {r['lookups']} | {r['doubts']} | {flag} | `{cell(r['file'])}` | "
                     f"{where} | {cell(r['why'] or r['how'])} |")
    if len(ranked) > top:
        lines.append(f"\n+{len(ranked) - top} more files")
    return "\n".join(lines)


def git_root():
    done = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else "."


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sources", nargs="*", help="session ids or export .json files (default: the database)")
    ap.add_argument("--repo", default=None, help="the repo whose files count (default: the current git root)")
    ap.add_argument("--db", default=None)
    ap.add_argument("--since", default="48h")
    ap.add_argument("--all", action="store_true", help="every session, not only subagents")
    ap.add_argument("--roles", action="store_true", help="every role, not only lanes")
    ap.add_argument("--lane", default=r"lane-brief", help="regex: a session whose brief or reads name it is a lane")
    ap.add_argument("--promote", type=int, default=3, help="sessions that make a file PROMOTE")
    ap.add_argument("--state", default=None, help="JSON file remembering promoted files (one alert each)")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args(argv)
    for src in a.sources:  # a stray `.` is no session id: never `opencode session export .`
        if pathlib.Path(src).is_dir():
            raise SystemExit(f"doubt: {src}: a directory; sources are session ids or export .json files; "
                             "omit for the database")
    root = pathlib.Path(a.repo or git_root())
    if not root.is_dir():
        raise SystemExit(f"doubt: --repo {root}: no such folder")
    scan = Scan(Repo(root), a.lane, a.roles)
    state = friction.load_state(a.state)
    try:  # friction's readers, so both tools read sessions alike
        if a.sources:
            for src in a.sources:
                friction.read_export(scan, src)
            label = "named"
        else:
            friction.read_db(scan, a.db or friction.default_db(), friction.since_ms(a.since), a.all)
            label = f"{'all' if a.all else 'subagents'}, last {a.since}"
    except SystemExit as e:
        raise SystemExit(str(e).replace("friction:", "doubt:", 1)) from None
    rows = scan.finish()
    new = promote(rows, a.promote, state)
    print(report(scan, rows, label, a.top))
    if a.state and new:
        p = pathlib.Path(a.state)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    if new:
        print(f"\nNEW PROMOTE: {len(new)}: " + "; ".join(f[:80] for f in new))
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
