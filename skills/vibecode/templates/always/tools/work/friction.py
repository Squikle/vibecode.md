"""Where sessions bump into their environment: failed tool calls, and error lines inside "successful" tool output
that a lane worked around and never reported. It clusters them, so a trap nobody wrote a rule for still surfaces.
The coordinator's Friction routine runs it every 4 hours (docs/workflow/coordinator-more.md).

Usage: python -I -X utf8 tools/work/friction.py [--db <opencode.db>] [--since 48h] [--all] [--promote 5]
                                                [--state <file>] [--top 15] [<session id or export .json> ...]
  With no session named it reads the opencode database read-only (`--db`, default `$XDG_DATA_HOME/opencode/
  opencode.db`, else `~/.local/share/opencode/opencode.db`: the same place on Windows, macOS and Linux): the
  sessions updated in `--since` (`90m`, `48h`, `7d`), subagents only (`--all`: every session). A named session id
  is read with `opencode session export <id>`, a path ending in .json as such an export; each is read whole. A
  directory (a stray `.`) is refused before anything runs.

What counts is the command's own failure, never the text it printed: a tool call that ended in error; in shell
output a shell's own complaint (a PowerShell parse or cmdlet error and its `| <message>` lines, git's `fatal:`, a
missing tool, a locked or denied file); and, from a command that exited non-zero and does not read or test, a line
shaped like an error (`...Error: `, `error: `, `N failed`). A command that reads a file or log (`Get-Content`,
`Select-String`, `cat`, `git show`, a `.log`) or runs tests (`pytest`, `playwright`, `gate.py`) prints other
programs' errors: only its own count. Each line is normalized (paths, shas, numbers and quoted text that is not one
word become `<path>`, `<sha>`, `N`, `<q>`) and clustered; a cluster holds its sessions, hits, one command and one
line. TRAPS names the known ones and their fix in one line; the rest are UNKNOWN, ranked by sessions: the point of
the report, a new trap shows before anyone writes its regex. A product test failing (FAILED, AssertionError) is
work, not friction: tagged `test`, never promoted. A lane's own `friction: <error> xN` line (AGENTS.md section 5)
adds N hits to its cluster.

A cluster seen in `--promote` sessions or more is PROMOTE: fix it at source. `--state <file>` remembers the
promoted ones (JSON); one alerts once, as NEW. `--fixed <trap>=<date>` (kept in `--state`) marks a trap fixed at
source: it shows `fixed <date>` with its hits and sessions since, and never promotes again (a hit since is a shell
opened before the fix, or a fix that did not take). Exit 1 when a NEW promote is in the report (no `--state`: any
promote), else 0, so an automation can alert. It prints a short markdown report to stdout and writes nothing but
`--state`. Check a cluster against its sessions before fixing it: a count alone can be a false alarm.
"""
import argparse, collections, json, os, pathlib, re, shutil, sqlite3, subprocess, sys, time

# Known traps: (name, line regex, command regex or None, the fix in one line). First match wins.
TRAPS = [
    ("rg-missing", r"\brg\b.*(not recognized|not found)|rg: (command )?not found", None,
     "install ripgrep, then open a fresh shell (an old one keeps the old PATH); else `git grep`"),
    ("git-grep-option-after-pattern", r"unable to resolve revision|must come before non-option arguments|"
     r"ambiguous argument '-", r"\bgit\b[^|;\n]*\bgrep\b",
     "git grep options go before the pattern: `git grep -n -A 8 -e <pat> -- <path>`"),
    ("git-missing-ref", r"Needed a single revision|unknown revision|ambiguous argument|bad revision", None,
     "check the ref first: `git branch --list <ref>` or `git rev-parse --verify --quiet <ref>`"),
    ("pwsh-substring", r"Substring|startIndex|Index and length must refer", None,
     "never `.Substring(0,N)` to trim (throws on a short line): `$s[0..199] -join ''` or `-replace '^(.{200}).*','$1'`"),
    ("pwsh-parse", r"Unexpected token|An empty pipe element is not allowed|Missing closing '\)'|"
     r"Expressions are only allowed as the first element of a pipeline|Variable reference is not valid|"
     r"Missing property name after reference operator|An expression was expected after|"
     r"Array index expression is missing|^ParserError: \S+\.ps1:\d+", None,
     "more than ~3 statements or nested quotes: write a script file and run it; `\"${x}:\"`, not `\"$x:\"`; "
     "`\\\"` is no escape in pwsh: single-quote a pattern holding `\"`"),
    ("pwsh-alias-shadow", r"Invoke-History|shadow\w* by (an |the |a built-in )?alias|alias \S+ shadow", None,
     "a pwsh function named like a built-in alias never runs (`R` is Invoke-History): `Get-Alias <name>` first, "
     "or a verb-noun name"),
    ("pwsh-console-mojibake", r"mojibake|mangl\w* (non-ASCII|unicode|utf)|â€|Ã[\x80-\xbf]", None,
     "a pwsh capture (`git show`, console output) mangles non-ASCII: compare revisions byte-wise in Python, "
     "`subprocess.run([...], capture_output=True)` with bytes"),
    ("edit-stale", r"Could not find oldString", None,
     "re-read the exact lines just before an edit; anchor on a small unique piece of them"),
    ("pytest-temp-race", r"(PermissionError|Access is denied|being used by another process).*pytest-(current|of-)", None,
     "a unique `--basetemp` per run (`--basetemp .work/pytest-<id>`): parallel runs share `pytest-current`"),
    ("python-charmap", r"'charmap' codec|UnicodeEncodeError|UnicodeDecodeError", None,
     "`PYTHONUTF8=1` as a user env var; run `python -I -X utf8`; open files with `encoding=\"utf-8\"`"),
    ("python-no-venv", r"ModuleNotFoundError|No module named", None,
     "run the project's venv python (`.venv/Scripts/python`, `.venv/bin/python`), not a bare `python`"),
    ("guessed-path", r"Cannot find path|Could not find a part of the path|No such file or directory|does not exist|"
     r"cannot find the (file|path)|File not found", None,
     "look before you read: list the folder (`glob`, `map.py`, `find.py`) instead of guessing a path"),
]
TRAPS = [(n, re.compile(l, re.I), re.compile(c, re.I) if c else None, f) for n, l, c, f in TRAPS]
# An error's shape, not just the word: an exception or `fatal:` leading the line, a shell's own complaint anywhere.
ERROR = re.compile(r"^\s*([\w.]*(Error|Exception)(: | calling |$)|(fatal|error|FATAL|ERROR)(\[\w+\])?: |FAILED\b)|"
                   r"is not recognized as|command not found|No such file or directory|Cannot find path|"
                   r"Could not find a part of the path|Permission denied|Access is denied|"
                   r"being used by another process|unable to resolve revision|Needed a single revision|"
                   r"positional parameter cannot be found|\d+ failed\b")
# pwsh's `     | <message>`, a word in it (not its `~~~` or `^` marks); a doc table starts at col 0
PWSH_MSG = re.compile(r"^\s{3,}\|\s+(?=[^|]*[A-Za-z])(?![~^\s])(?!.*\|\s*$)")
# Not an error, though it reads like one: a listing (`12: `, `path:12:`, a diff line), code, docs, JSON, a bare
# category header (`ParserError:`, its message follows on a `|` line).
NOT_ERROR = re.compile(r"^\s*(\d+[:\t]|[+-](?![+-])|(?:[A-Za-z]:)?[^\s:|]+:\d+[:-]|[>#*\"'{}()\[/]|except\b|raise\b|"
                       r"catch\b|throw\b|if\b|elif\b|return\b|def\b|class\b|import\b|from\b|- |0 errors?\b|"
                       r"no errors?\b|errors?: 0\b|Traceback \(most recent)|^\s*\w+(Error|Exception):?\s*$")
TEST_RUN = re.compile(r"pytest|vitest|jest|playwright|unittest|go test|cargo test|\b(npm|pnpm|yarn) (run )?test|"
                      r"mocha|rspec|phpunit|ctest|gate\.py", re.I)
# A command that reads a file or a log: what it prints is someone else's text, errors included.
READS = re.compile(r"\b(Get-Content|gc|cat|head|tail|Select-String|sls|findstr|grep|rg|jq|Import-Csv|"
                   r"ConvertFrom-Json)\b|\bgit\s+(show|log|diff|blame|grep)\b|\.(log|out)\b", re.I)
# The command's own failure, wherever it ran: the shell's, git's or the OS's complaint, not a program's text.
VERBS = (r"Get|Set|Select|Where|ForEach|Remove|New|Copy|Move|Out|Write|Invoke|Start|Stop|Test|Import|Export|"
         r"ConvertFrom|ConvertTo|Add|Join|Split|Resolve|Push|Pop|Measure|Sort|Group|Format|Read|Clear|Rename|Wait|"
         r"Compare")
OWN = re.compile(r"is not recognized as|command not found|No module named|'charmap' codec|Permission denied|"
                 r"Access is denied|being used by another process|No such file or directory|Cannot find path|"
                 r"Could not find a part of the path|Exception calling |^\s*fatal: |"
                 rf"^\s*({VERBS})-[A-Z]\w+: \S|^\s*(InvalidOperation|ParserError|MethodInvocationException|"
                 r"RuntimeException|ParameterBindingException|CommandNotFoundException|InvalidArgument|"
                 r"ObjectNotFound|PermissionDenied|ResourceUnavailable|NotSpecified): \S|Unexpected token|"
                 r"An empty pipe element|Missing closing|Expressions are only allowed|Variable reference is not valid|"
                 r"Missing property name after|An expression was expected after|Array index expression is missing")
TEST_LINE = re.compile(r"\bFAILED\b|\bFAIL\b|AssertionError|\bassert\b|\d+ failed|expect\(|Test timeout|"
                       r"Timeout \d+ms exceeded|locator\.\w+:|page\.\w+:|toBe|toHave|toEqual|✘|×", re.I)
SELF = re.compile(r"(?im)^[\s>*_`-]*friction:\s*(.+?)\s*[`*_]*\s*[x×]\s*(\d+)\s*[`*_.]*\s*$")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\[[0-9;]+m")  # a colour code, with or without its ESC
LINES_PER_OUTPUT = 400


def norm(line):
    s = ANSI.sub("", line).strip()
    s = re.sub(r"^\|\s*", "", s)  # PowerShell's `     | <message>`
    s = re.sub(r"https?://\S+", "<url>", s)
    s = re.sub(r"(?:[A-Za-z]:|\\\\[\w.$-]+)[\\/][^\s'\"]*|(?<![\w.])~?/[\w.@~+-]+(?:/[\w.@~+-]*)+", "<path>", s)
    s = re.sub(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b", "<sha>", s)
    s = re.sub(r"'([^']*)'|\"([^\"]*)\"",
               lambda m: m.group(0) if re.fullmatch(r"[A-Za-z_][\w.-]{0,30}", m.group(1) or m.group(2) or "") else "<q>", s)
    s = re.sub(r"\d+", "N", s)
    return re.sub(r"\s+", " ", s)[:140]


def text_of(st):
    out = st.get("content")
    if isinstance(out, list):
        out = "\n".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in out)
    elif isinstance(out, dict):
        out = json.dumps(out)
    if not out and isinstance(st.get("output"), str):
        out = st["output"]
    return str(out or "")


def one_line(s, n):
    return re.sub(r"\s+", " ", ANSI.sub("", str(s))).strip()[:n]


class Scan:
    def __init__(self):
        self.clusters = {}
        self.sessions, self.calls, self.failed = set(), 0, 0

    def add(self, sid, t, key, line, cmd, tag, trap=None, hits=1, self_report=False):
        c = self.clusters.setdefault(key, {"key": key, "tag": tag, "trap": trap, "sessions": set(), "hits": 0,
                                           "self": 0, "times": [], "line": one_line(line, 160),
                                           "cmd": one_line(cmd, 120)})
        c["sessions"].add(sid); c["hits"] += hits; c["self"] += hits if self_report else 0
        c["times"].append((t, sid, hits))
        if not c["cmd"] and cmd:
            c["cmd"] = one_line(cmd, 120)

    def classify(self, sid, t, line, cmd, own=False, self_report=False, hits=1):
        """`own`: the command's own failure (a tool error, a shell's complaint), never a test result."""
        for name, rx, crx, fix in TRAPS:
            if rx.search(line) and (crx is None or crx.search(cmd)):
                return self.add(sid, t, "trap:" + name, line, cmd, "known", (name, fix), hits, self_report)
        key = norm(line)
        if not (own or self_report) and TEST_LINE.search(line):
            return self.add(sid, t, "test:" + key, line, cmd, "test", None, hits)
        self.add(sid, t, key, line, cmd, "unknown", None, hits, self_report)

    def part(self, sid, p, assistant=True, t=None):
        """One part of a session: a tool call (its error, or error lines in a shell's output) or a text (self-reports)."""
        self.sessions.add(sid)
        st = p.get("state") if isinstance(p.get("state"), dict) else {}
        t = t or (st.get("time") or {}).get("start") or (p.get("time") if isinstance(p.get("time"), dict) else {}).get("start")
        if p.get("type") == "text":
            for m in SELF.finditer(p.get("text") or "") if assistant else ():
                self.classify(sid, t, m.group(1).strip("`*_ "), "", self_report=True, hits=int(m.group(2)))
            return
        if p.get("type") != "tool":
            return
        inp = st.get("input") or {}
        tool = p.get("name") or p.get("tool") or "?"
        cmd = str(inp.get("command") or inp.get("code") or inp.get("path") or inp.get("filePath") or
                  inp.get("pattern") or "")
        self.calls += 1
        if st.get("status") == "error":
            self.failed += 1
            err = st.get("error")
            err = err.get("message", json.dumps(err)) if isinstance(err, dict) else str(err or "error")
            return self.classify(sid, t, f"{tool}: {err.splitlines()[0] if err else 'error'}", cmd, own=True)
        if tool not in ("shell", "bash"):  # read/grep outputs are file contents: an `Error` there is code
            return
        code = (st.get("metadata") or {}).get("exit")
        failed = code != 0  # no exit code known: as if it failed
        quiet = bool(READS.search(cmd) or TEST_RUN.search(cmd))
        if quiet and not failed:
            return  # a read or a test run that worked: every error in it is the file's or the product's
        # Error-shaped lines count only from a failed command that neither reads nor tests; otherwise only the
        # command's own complaints (OWN, and pwsh's `| <message>` lines).
        loud = failed and not quiet
        msg = []  # a PowerShell message wraps over several `     | ` lines: one error
        for line in text_of(st).splitlines()[:LINES_PER_OUTPUT] + [""]:
            clean = ANSI.sub("", line)
            if PWSH_MSG.match(clean):
                msg.append(clean.strip().lstrip("| ").strip())
                continue
            if msg:
                self.classify(sid, t, " ".join(msg)[:600], cmd, own=True)
                msg = []
            if len(clean) >= 600 or NOT_ERROR.search(clean):
                continue
            if OWN.search(clean):
                self.classify(sid, t, clean.strip(), cmd, own=True)
            elif loud and ERROR.search(clean):
                self.classify(sid, t, clean.strip(), cmd)


def default_db():
    base = os.environ.get("XDG_DATA_HOME") or str(pathlib.Path.home() / ".local" / "share")
    return pathlib.Path(base) / "opencode" / "opencode.db"


def since_ms(text):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", text.strip())
    if not m:
        raise SystemExit(f"friction: --since {text}: want 90m, 48h or 7d")
    sec = float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "": 3600}[m.group(2)]
    return int((time.time() - sec) * 1000)


def read_db(scan, path, since, everyone):
    if not pathlib.Path(path).is_file():
        raise SystemExit(f"friction: no database at {path} (--db, or name session exports)")
    db = sqlite3.connect(pathlib.Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    if not {"session_v2", "session_part"} <= tables:
        raise SystemExit("friction: no session_v2/session_part tables: name exports (opencode session export <id>)")
    kids = {r[0] for r in db.execute("select id from session_v2 where time_updated > ?" +
                                     ("" if everyone else " and parent_id is not null"), (since,))}
    role = "m.type" if "session_message" in tables else "null"
    join = " left join session_message m on m.id = p.message_id" if "session_message" in tables else ""
    rows = db.execute(f"select p.session_id, p.data, {role}, p.time_created from session_part p{join} where p.type in"
                      " ('tool', 'text') and p.time_created > ? order by p.session_id, p.time_created, p.ordinal",
                      (since,))
    for sid, data, kind, t in rows:
        if sid in kids:
            try:
                scan.part(sid, json.loads(data), assistant=kind in (None, "assistant"), t=t)
            except (ValueError, AttributeError, TypeError):
                continue
    db.close()


def read_export(scan, src):
    if src.endswith(".json"):
        p = pathlib.Path(src)
        text = p.read_text(encoding="utf-8-sig") if p.is_file() else ""
    else:
        exe = shutil.which("opencode") or "opencode"
        text = subprocess.run([exe, "session", "export", src], capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout
    i = text.find("{")
    if i < 0:
        raise SystemExit(f"friction: {src}: no export")
    o = json.loads(text[i:])
    sid = (o.get("info") or {}).get("id") or src
    for m in o.get("messages", []):
        for p in m.get("content") or m.get("parts") or []:
            scan.part(sid, p, assistant=m.get("type", (m.get("info") or {}).get("role")) == "assistant")


def load_state(path):
    if not path or not pathlib.Path(path).is_file():
        return {"promoted": {}}
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8")) or {"promoted": {}}
    except ValueError:
        return {"promoted": {}}


def when_ms(text):
    """A local date or time (`2026-11-26`, `2026-11-26T14:30`) in epoch ms."""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(time.mktime(time.strptime(text.strip(), fmt)) * 1000)
        except ValueError:
            continue
    raise SystemExit(f"friction: {text}: want a date like 2026-11-26 or 2026-11-26T14:30")


def mark_fixed(scan, fixed):
    """A trap fixed at source: its hits and sessions since the fix; it never promotes again."""
    for c in scan.clusters.values():
        date = fixed.get(c["trap"][0]) if c["trap"] else None
        if date:
            ms = when_ms(date)
            since = [(sid, h) for t, sid, h in c["times"] if t and t >= ms]
            c["fixed"] = (date, len({sid for sid, _ in since}), sum(h for _, h in since))


def promote(scan, n, state):
    """Mark PROMOTE clusters; answers the keys new to `state` (and records them there)."""
    new, done = [], state.setdefault("promoted", {})
    for c in scan.clusters.values():
        c["promote"] = c["tag"] != "test" and len(c["sessions"]) >= n and not c.get("fixed")
        c["new"] = c["promote"] and c["key"] not in done
        if c["new"]:
            new.append(c["key"])
            done[c["key"]] = {"first": time.strftime("%Y-%m-%dT%H:%M"), "sessions": len(c["sessions"])}
    return new


def cell(s):
    return str(s).replace("|", "\\|").replace("`", "'")


def report(scan, label, top):
    cs = list(scan.clusters.values())
    rank = lambda c: (-len(c["sessions"]), -c["hits"], c["key"])
    pct = f"{100 * scan.failed / scan.calls:.1f}%" if scan.calls else "-"
    lines = [f"# Friction: {len(scan.sessions)} sessions ({label}), {scan.calls} tool calls, {scan.failed} failed "
             f"({pct}), {sum(c['hits'] for c in cs if c['tag'] != 'test')} error lines in {len(cs)} clusters", ""]
    def flag(c):
        if c.get("fixed"):
            date, ss, hs = c["fixed"]
            return f"fixed {date}: {hs} hits in {ss} sessions since"
        return ("PROMOTE NEW" if c["new"] else "PROMOTE") if c["promote"] else ""
    unknown = sorted((c for c in cs if c["tag"] == "unknown"), key=rank)
    lines += ["## Unknown, by sessions", "", "| sessions | hits | flag | line | example command |", "|---|---|---|---|---|"]
    hits = lambda c: f"{c['hits']} ({c['self']} self)" if c["self"] else str(c["hits"])
    lines += [f"| {len(c['sessions'])} | {hits(c)} | {flag(c)} | `{cell(c['line'])}` | `{cell(c['cmd'])}` |"
              for c in unknown[:top]]
    if len(unknown) > top:
        lines.append(f"\n+{len(unknown) - top} more unknown clusters")
    known = sorted((c for c in cs if c["tag"] == "known"), key=rank)
    lines += ["", "## Known traps", "", "| sessions | hits | flag | trap | fix |", "|---|---|---|---|---|"]
    lines += [f"| {len(c['sessions'])} | {hits(c)} | {flag(c)} | {c['trap'][0]} | {cell(c['trap'][1])} |" for c in known]
    tests = [c for c in cs if c["tag"] == "test"]
    lines += ["", f"Tests (work, not friction): {len(tests)} clusters, {sum(c['hits'] for c in tests)} lines, "
              f"{len(set().union(*(c['sessions'] for c in tests))) if tests else 0} sessions."]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sources", nargs="*", help="session ids or export .json files (default: the database)")
    ap.add_argument("--db", default=None)
    ap.add_argument("--since", default="48h")
    ap.add_argument("--all", action="store_true", help="every session, not only subagents")
    ap.add_argument("--promote", type=int, default=5, help="sessions that make a cluster PROMOTE")
    ap.add_argument("--state", default=None, help="JSON file remembering promoted clusters (one alert each)")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--fixed", action="append", default=[], metavar="TRAP=DATE",
                    help="a known trap fixed at source on DATE (kept in --state); repeatable")
    a = ap.parse_args(argv)
    names = {name for name, *_ in TRAPS}
    state = load_state(a.state)
    fixed = state.setdefault("fixed", {})
    changed = False
    for f in a.fixed:
        name, _, date = f.partition("=")
        if name not in names:
            raise SystemExit(f"friction: --fixed {f}: unknown trap; known: {', '.join(sorted(names))}")
        when_ms(date)
        changed |= fixed.get(name) != date.strip()
        fixed[name] = date.strip()
    scan = Scan()
    for src in a.sources:  # a stray `.` after the command line is no session id: never `opencode session export .`
        if pathlib.Path(src).is_dir():
            raise SystemExit(f"friction: {src}: a directory; sources are session ids or export .json files; "
                             "omit for the database")
    if a.sources:
        for src in a.sources:
            read_export(scan, src)
        label = "named"
    else:
        read_db(scan, a.db or default_db(), since_ms(a.since), a.all)
        label = f"{'all' if a.all else 'subagents'}, last {a.since}"
    mark_fixed(scan, fixed)
    new = promote(scan, a.promote, state)
    print(report(scan, label, a.top))
    if a.state and (new or changed):
        p = pathlib.Path(a.state)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    if new:
        print(f"\nNEW PROMOTE: {len(new)}: " + "; ".join(k.removeprefix("trap:")[:80] for k in new))
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
