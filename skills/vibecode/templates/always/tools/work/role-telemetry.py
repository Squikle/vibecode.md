"""Which raw commands each role runs where a project tool would do, which helpers it types inline again and again,
and which files it reads that the knowledge map never gave it. Zero model tokens: it reads the opencode database.

Usage: python -I -X utf8 tools/work/role-telemetry.py [--db <opencode.db>] [--since 24h] [--project <text>]
                                                      [--state <file>] [--rebaseline] [--log <file>] [--json]
                                                      [--seen-dir <dir>] [--flag-share 0.15] [--flag-min 20]
       python -I -X utf8 tools/work/role-telemetry.py --examples <role> <rule> [--since 7d]
  Reads `session_v2`, `session_message` and `session_part` read-only (`--db`, default `$XDG_DATA_HOME/opencode/
  opencode.db`, else `~/.local/share/opencode/opencode.db`), the sessions updated in `--since` (`90m`, `24h`, `7d`),
  `--project`: only sessions whose directory holds that text.

A session's role: its `agent` when that is a role (old names map: planner -> product, cto -> architect), else the
brief it was started with (the first user message), else a tag at the start of its title, else `other`. Each finished
tool call is one of: ROLE-TOOL (a `tools/**` script ran), REPLACEABLE (a raw command a project tool covers: RULES,
each with the tool to use), ADHOC (an inline script: a here-string into an interpreter, `python -c`), RAW (any other
shell) or BUILTIN (read, grep, edit...; a whole read of a doc that is searched by section counts as REPLACEABLE).
Inline helper definitions (`function cut {...}` typed into a shell) are counted by name: one typed in many sessions
is a missing tool. Off-map reads: in a session that asked `kmap.py` (its `.work/kmap/seen/<session>.json`), a read of
a doc or code file the map never returned, start reads excepted; a high share means the map lacks links or the role
ignores it.

Flags: a role's replaceable share at or over `--flag-share` with `--flag-min` calls; a role with 3+ sessions and no
tool call at all (a tool broke or a brief lost its line); an inline helper defined 10+ times in 3+ sessions; half or
more of 20+ reads off the map. With `--state <file>` the first run keeps each role's share and top three rules as its
baseline and flags no share; later runs flag a share 5 points over the baseline and a rule new in a role's top three;
each flag alerts once, as NEW (`--rebaseline` starts over). Exit 1 when a NEW flag is in the report (no `--state`: any
flag), else 0, so a no-model automation can alert. `--log <file>` appends each NEW flag as a vault line
(`... TOOLUSE flag= role= rule= n= share= hint="..."`). `--examples <role> <rule>` prints up to five of those commands,
to check a flag before acting on it (counts are upper bounds until a rule's precision is checked). It prints markdown
to stdout and writes nothing but `--state` and `--log`. The rungs from a flag to a fix: docs/workflow/telemetry.md.
"""
import argparse
import collections
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import time

ROLES = {"coordinator", "product", "architect", "plan-review", "pregroomer", "executor", "reviewer", "integrator",
         "librarian", "helper", "builder"}
ALIASES = {"planner": "product", "cto": "architect", "pre-groomer": "pregroomer", "scout": "pregroomer",
           "plan-reviewer": "plan-review", "lane": "executor"}
BY_BRIEF = [  # the first user message; first match wins
    ("plan-review", r"workflow[\\/]plan-review\.md|You review one planning pass"),
    ("reviewer", r"reviewer-brief|You review one batch"),
    ("integrator", r"integrator-brief|You are the integrator"),
    ("executor", r"lane-brief|You are executor|You are lane"),
    ("pregroomer", r"workflow[\\/]pregroomer\.md|You are the pre-groomer"),
    ("architect", r"workflow[\\/](architect|cto)\.md|You are the (architect|CTO)\b"),
    ("librarian", r"workflow[\\/]librarian\.md|You are the librarian"),
    ("product", r"workflow[\\/]product\.md|docs[\\/]PLANNING\.md|You are the (product lead|planner)\b"),
    ("coordinator", r"workflow[\\/]coordinator\.md|coordinator-now|You are the coordinator"),
    ("builder", r"You are the builder"),
    ("helper", r"^Answer (this|these) question"),
]
BY_TITLE = [("plan-review", r"^(PLANREV|Plan review)"), ("reviewer", r"^(REV|Review)"),
            ("architect", r"^(ARCH|CTO)"), ("product", r"^(PROD|PLAN|Planner)"), ("pregroomer", r"^(PREGROOM|Pre-groom|SCOUT)"),
            ("librarian", r"^(LIB|Librarian)"), ("integrator", r"^(INT\d|Integrat)"), ("coordinator", r"[Cc]oordinator")]
# (name, a raw command's pattern, the tool that covers it, the roles it applies to or None for all)
RULES = [
    ("code-def-grep", r"\b(git\s+grep|grep|rg|select-string|sls)\b.*(\b(function|def|class|interface)\s+\w|"
                      r"export\s+(async\s+)?(function|const|class)\b)", "map.py --find <name>", None),
    ("docs-grep", r"\b(git\s+grep|grep|rg|select-string|sls|findstr)\b[^|;]*(\bdocs[\\/]|BACKLOG|ROADMAP|DECISIONS|"
                  r"QUESTIONS|HANDOVER|ARCHITECTURE|\bspecs[\\/])", "find.py <term> / kmap.py <topic>", None),
    ("doc-whole-read", r"\b(get-content|cat|type|gc)\b\s+[^|;]*(\bdocs[\\/][^\s|;]+|AGENTS)\.md\b"
                       r"(?![^|;]*(-tail|-totalcount|-head|-first|-skip|select-object|-last))",
     "find.py --toc / --section", None),
    ("status-sweep", r"\bgit\s+worktree\s+list\b|\bgit\s+log\s+-1\b.*\b(for|foreach)\b|get-childitem[^|;]*\.work",
     "coord-beat.py", {"coordinator"}),
    ("session-peek", r"opencode\s+(session|export)|sqlite3|opencode\.db", "session-audit.py --peek / coord-beat.py",
     {"coordinator"}),
    ("review-diff", r"\bgit\s+(diff|show|log)\b[^|;]*\.\.\.?", "review-pack.py <branch>", {"reviewer"}),
    ("history-dig", r"\bgit\s+(log|blame)\b[^|;]*(-L|--follow|-S|-G|--name-only)", "a doubt: line, then one blame",
     {"executor"}),
    ("polling", r"start-sleep|\bsleep\s+\d|wait-process[^|;]*-timeout", "a foreground run / the background notice", None),
    ("backlog-edit", r"(set-content|out-file|writealltext|add-content)[^|;]*BACKLOG", "order.py word (rows by id)", None),
]
RULES = [(n, re.compile(p, re.I), h, r) for n, p, h, r in RULES]
HINT = {n: h for n, _, h, _ in RULES}
SEARCHED = re.compile(r"(^|[\\/])docs[\\/](DECISIONS|QUESTIONS|ROADMAP|MEASUREMENTS|MERGES)\.md$", re.I)
HINT["searched-doc-whole"] = "find.py --section / kmap.py <topic> --level 3"
SEGMENT = re.compile(r"[;\n]|\|(?!\|)|&&")
ROLE_TOOL = re.compile(r"\b(python3?|py|pwsh|powershell|bash|sh|node)(\.exe)?\b[^|;\n]*?\btools[\\/][\w./\\-]+\."
                       r"(py|ps1|sh|mjs|js)\b|(^|[;&|]\s*)\.?[\\/]?tools[\\/][\w./\\-]+\.(ps1|sh)\b", re.I)
ADHOC = re.compile(r"@['\"]\s*\n[\s\S]*['\"]@\s*\|\s*(python3?|node|pwsh)|\b(python3?|py)(\.exe)?\s+(-I\s+)?"
                   r"(-X\s+\S+\s+)?-c\b|\bnode\s+-e\b|\$\w+\s*=\s*@['\"]\s*\n", re.I)
INLINE_FN = re.compile(r"(?:^|[;\n{])\s*function\s+([A-Za-z_][\w-]*)|(?:^|[;\n])\s*([A-Za-z_]\w*)\s*\(\)\s*\{", re.I)
NORM = [(re.compile(r"(?:[A-Za-z]:|~)?[\\/][^\s'\"|;]+"), "<path>"), (re.compile(r"\b[0-9a-f]{7,40}\b"), "<sha>"),
        (re.compile(r"'[^']*'|\"[^\"]*\""), "<q>"), (re.compile(r"\d+"), "N")]
READ_KINDS = re.compile(r"\.(md|ts|tsx|js|jsx|mjs|py|css|go|rs|java|kt|cs|rb|php|vue|svelte)$", re.I)
START_READS = re.compile(r"(^|/)(AGENTS\.md|docs/workflow/|\.work/)", re.I)


def default_db():
    base = os.environ.get("XDG_DATA_HOME") or str(pathlib.Path.home() / ".local" / "share")
    return pathlib.Path(base) / "opencode" / "opencode.db"


def default_seen():
    """`.work/kmap/seen/` in the main checkout of the repository this tool lives in."""
    root = pathlib.Path(__file__).resolve().parents[2]
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-common-dir"], capture_output=True, text=True)
        common = pathlib.Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except OSError:
        common = None
    if common:
        common = common if common.is_absolute() else root / common
        root = common.resolve().parent if common.name == ".git" else root
    return root / ".work" / "kmap" / "seen"


def since_ms(text):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", text.strip())
    if not m:
        raise SystemExit(f"role-telemetry: --since {text}: want 90m, 24h or 7d")
    return int((time.time() - float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "": 3600}[m.group(2)]) * 1000)


def norm(cmd):
    s = cmd.strip().splitlines()[0] if cmd.strip() else ""
    for rx, rep in NORM:
        s = rx.sub(rep, s)
    return re.sub(r"\s+", " ", s)[:90]


def role_of(agent, brief, title):
    agent = ALIASES.get((agent or "").lower(), (agent or "").lower())
    if agent in ROLES:
        return agent
    for name, pat in BY_BRIEF:
        if brief and re.search(pat, brief[:3000], re.I | re.M):
            return name
    for name, pat in BY_TITLE:
        if re.search(pat, title or ""):
            return name
    return "other"


def classify(tool, inp, role):
    """(kind, key) for one tool call."""
    if tool not in ("shell", "bash"):
        path = str(inp.get("path") or inp.get("filePath") or "")
        if tool == "read" and not (inp.get("offset") or inp.get("limit")) and SEARCHED.search(path.replace("\\", "/")):
            return "REPLACEABLE", "searched-doc-whole"
        return "BUILTIN", tool + ("-whole" if tool == "read" and not (inp.get("offset") or inp.get("limit")) else "")
    c = inp.get("command") or ""
    if m := ROLE_TOOL.search(c):
        t = re.search(r"tools[\\/]([\w./\\-]+\.(?:py|ps1|sh|mjs|js))\s*([\w-]*)", c[m.start():], re.I)
        return "ROLE-TOOL", (t.group(1).replace("\\", "/") + " " + t.group(2)).strip() if t else norm(c)
    segs = [s for s in SEGMENT.split(c) if s.strip()]  # one segment at a time: no match across a pipe or `;`
    for name, rx, hint, roles in RULES:
        if (roles is None or role in roles) and any(rx.search(s) for s in segs):
            return "REPLACEABLE", name
    if ADHOC.search(c):
        return "ADHOC", norm(c)
    return "RAW", norm(c)


class Scan:
    def __init__(self):
        self.roles = {}
        self.stat = collections.defaultdict(lambda: {"sessions": set(), "kinds": collections.Counter(),
                                                     "rules": collections.Counter(), "raw": collections.Counter(),
                                                     "reads": 0, "offmap": 0, "mapped": set()})
        self.fns = collections.defaultdict(lambda: [0, set(), set()])  # name -> defs, sessions, roles
        self.examples = collections.defaultdict(list)
        self.read_paths = collections.defaultdict(list)                # session -> read paths

    def call(self, sid, p):
        st = p.get("state") or {}
        if st.get("status") not in ("completed", "error") or not isinstance(st.get("input"), dict):
            return
        role = self.roles[sid]
        s = self.stat[role]
        s["sessions"].add(sid)
        tool, inp = p.get("name", "?"), st["input"]
        kind, key = classify(tool, inp, role)
        s["kinds"][kind] += 1
        if kind == "REPLACEABLE":
            s["rules"][key] += 1
            if len(self.examples[(role, key)]) < 5:
                self.examples[(role, key)].append(str(inp.get("command") or inp.get("path") or "")[:200])
        elif kind in ("RAW", "ADHOC"):
            s["raw"][key] += 1
        if tool in ("shell", "bash"):
            for a, b in INLINE_FN.findall(inp.get("command") or ""):
                f = self.fns[(a or b).lower()]
                f[0] += 1
                f[1].add(sid)
                f[2].add(role)
        if tool == "read":
            path = str(inp.get("path") or inp.get("filePath") or "").replace("\\", "/")
            if READ_KINDS.search(path) and not START_READS.search(path):
                self.read_paths[sid].append(path)

    def offmap(self, seen_dir):
        """Per role: reads in sessions that asked the map, and those of a file it never returned."""
        for sid, paths in self.read_paths.items():
            f = seen_dir / f"{re.sub(r'[^\w-]', '', sid)}.json" if seen_dir else None
            if not f or not f.is_file():
                continue
            try:
                given = [g.lower() for g in json.loads(f.read_text(encoding="utf-8")).get("paths", [])]
            except (OSError, ValueError):
                continue
            s = self.stat[self.roles[sid]]
            s["mapped"].add(sid)
            for p in paths:
                s["reads"] += 1
                low = p.lower()
                if not any(low == g or low.endswith("/" + g) for g in given):
                    s["offmap"] += 1


def read_db(scan, path, since, project):
    if not pathlib.Path(path).is_file():
        raise SystemExit(f"role-telemetry: no database at {path} (--db)")
    db = sqlite3.connect(pathlib.Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    cols = {r[1] for r in db.execute("pragma table_info(session_v2)")}
    if not {"id", "time_updated"} <= cols:
        raise SystemExit("role-telemetry: no session_v2 table with id and time_updated")
    pick = ", ".join(c if c in cols else f"null as {c}" for c in ("id", "agent", "title", "directory"))
    sess = {}
    for sid, agent, title, directory in db.execute(f"select {pick} from session_v2 where time_updated > ?", (since,)):
        if project and project.lower() not in (directory or "").lower():
            continue
        sess[sid] = [agent, title, None]
    for sid, data in db.execute("select session_id, data from session_message where type = 'user' and "
                                "time_created > ? order by session_id, seq", (since,)):
        if sid in sess and sess[sid][2] is None:
            try:
                sess[sid][2] = json.loads(data).get("text") or None
            except (ValueError, AttributeError):
                pass
    missing = {sid for sid, v in sess.items() if v[2] is None}
    if missing:  # the brief as the session's first text part
        for sid, data in db.execute("select session_id, data from session_part where type = 'text' and "
                                    "time_created > ? order by session_id, time_created", (since,)):
            if sid in missing:
                try:
                    sess[sid][2] = json.loads(data).get("text") or ""
                except (ValueError, AttributeError):
                    sess[sid][2] = ""
                missing.discard(sid)
    scan.roles = {sid: role_of(agent, brief, title) for sid, (agent, title, brief) in sess.items()}
    for sid, data in db.execute("select session_id, data from session_part where type = 'tool' and time_created > ?"
                                " order by session_id, time_created", (since,)):
        if sid in scan.roles:
            try:
                scan.call(sid, json.loads(data))
            except (ValueError, AttributeError, TypeError):
                continue
    db.close()


def summary(scan):
    out = {}
    for role, s in scan.stat.items():
        k = s["kinds"]
        shell = k["ROLE-TOOL"] + k["REPLACEABLE"] + k["ADHOC"] + k["RAW"]
        out[role] = {"sessions": len(s["sessions"]), "calls": sum(k.values()), "role_tool": k["ROLE-TOOL"],
                     "replaceable": k["REPLACEABLE"], "adhoc": k["ADHOC"], "raw": k["RAW"], "builtin": k["BUILTIN"],
                     "share": round(k["REPLACEABLE"] / shell if shell else 0, 3),  # of its shell calls (+ such reads)
                     "rules": dict(s["rules"].most_common()), "raw_top": dict(s["raw"].most_common(5)),
                     "reads": s["reads"], "offmap": s["offmap"], "mapped_sessions": len(s["mapped"])}
    return out


def flags_of(roles, scan, a, base):
    """[(key, text, role, rule, n, share)] for this run; `base` is the baseline (None: no --state)."""
    flags = []
    for role, o in sorted(roles.items()):
        top3 = list(o["rules"])[:3]
        if base is None or role not in base:
            hit = base is None and o["replaceable"] >= a.flag_min and o["share"] >= a.flag_share
        else:
            hit = o["replaceable"] >= a.flag_min and o["share"] >= base[role]["share"] + 0.05
        if hit:
            r = top3[0] if top3 else ""
            flags.append((f"share:{role}", f"{role}: {o['replaceable']} replaceable calls ({o['share']:.0%} of its "
                          f"shell calls); top " + ", ".join(f"{n} x{o['rules'][n]} -> {HINT[n]}" for n in top3),
                          role, r, o["replaceable"], o["share"]))
        if base and role in base:
            for n in top3:
                if n not in base[role]["top3"] and o["rules"][n] >= a.flag_min:
                    flags.append((f"top3:{role}:{n}", f"{role}: {n} x{o['rules'][n]} is new in its top three -> "
                                  f"{HINT[n]}", role, n, o["rules"][n], o["share"]))
        if o["sessions"] >= 3 and o["role_tool"] == 0 and role not in ("other", "helper"):
            flags.append((f"silent:{role}", f"{role}: {o['sessions']} sessions and no tools/ script call at all "
                          "(a tool broke, or its brief lost the line)", role, "", 0, 0))
        if o["reads"] >= 20 and o["offmap"] * 2 >= o["reads"]:
            flags.append((f"offmap:{role}", f"{role}: {o['offmap']} of {o['reads']} reads off the map in "
                          f"{o['mapped_sessions']} sessions that asked it (missing links, or the map ignored)",
                          role, "offmap", o["offmap"], round(o["offmap"] / o["reads"], 3)))
    for name, (defs, sessions, rs) in sorted(scan.fns.items()):
        if defs >= 10 and len(sessions) >= 3:
            flags.append((f"inline:{name}", f"inline helper `{name}` typed {defs} times in {len(sessions)} sessions "
                          f"({', '.join(sorted(rs))}): a missing tool?", ",".join(sorted(rs)), name, defs, 0))
    return flags


def report(roles, scan, flags, new, label):
    rows = [f"# Role tool use: {sum(o['sessions'] for o in roles.values())} sessions {label}", "",
            "| Role | Sessions | tools/ runs | Replaceable | Share | Ad-hoc | Off-map reads | Top replaceable -> tool |",
            "|---|---:|---:|---:|---:|---:|---:|---|"]
    for role, o in sorted(roles.items(), key=lambda kv: -kv[1]["calls"]):
        top = "; ".join(f"{n} x{c} -> {HINT[n]}" for n, c in list(o["rules"].items())[:3]) or "-"
        off = f"{o['offmap']}/{o['reads']}" if o["mapped_sessions"] else "-"
        rows.append(f"| {role} | {o['sessions']} | {o['role_tool']} | {o['replaceable']} | {o['share']:.0%} | "
                    f"{o['adhoc']} | {off} | {top} |")
    helpers = sorted(((d, len(s), n, r) for n, (d, s, r) in scan.fns.items()), reverse=True)[:8]
    if helpers:
        rows += ["", "## Inline helpers typed into shells", "", "| Name | Times | Sessions | Roles |", "|---|---:|---:|---|"]
        rows += [f"| `{n}` | {d} | {s} | {', '.join(sorted(r))} |" for d, s, n, r in helpers]
    rows += ["", "## Flags", ""] + ([f"- {'NEW ' if k in new else ''}{t}" for k, t, *_ in flags] or ["- none"])
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--db", default=None)
    ap.add_argument("--since", default="24h")
    ap.add_argument("--project", default="")
    ap.add_argument("--state", default=None)
    ap.add_argument("--rebaseline", action="store_true")
    ap.add_argument("--log", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--seen-dir", default=None)
    ap.add_argument("--flag-share", type=float, default=0.15)
    ap.add_argument("--flag-min", type=int, default=20)
    ap.add_argument("--examples", nargs=2, metavar=("ROLE", "RULE"))
    a = ap.parse_args(argv)
    scan = Scan()
    read_db(scan, a.db or default_db(), since_ms(a.since), a.project)
    if a.examples:
        role, rule = ALIASES.get(a.examples[0], a.examples[0]), a.examples[1]
        got = scan.examples.get((role, rule), [])
        print(f"# {role} / {rule}: {len(got)} examples (of {scan.stat[role]['rules'][rule]})")
        print("\n".join(f"- `{g}`" for g in got) or "- none")
        return 0
    seen = pathlib.Path(a.seen_dir) if a.seen_dir else default_seen()
    scan.offmap(seen if seen.is_dir() else None)
    roles = summary(scan)
    state_path = pathlib.Path(a.state) if a.state else None
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path and state_path.is_file() else {}
    if a.rebaseline:
        state = {}
    base = state.get("baseline") if state_path else None
    if state_path and not base:  # the first run is the baseline: no share is flagged against itself
        base = {r: {"share": o["share"], "top3": list(o["rules"])[:3]} for r, o in roles.items()}
    flags = flags_of(roles, scan, a, base)
    alerted = set(state.get("alerted", []))
    new = {k for k, *_ in flags} - alerted if state_path else {k for k, *_ in flags}
    if a.json:
        print(json.dumps({"roles": roles, "flags": [{"key": k, "text": t, "new": k in new} for k, t, *_ in flags]},
                         indent=1))
    else:
        print(report(roles, scan, flags, new, f"since {a.since}"))
    if state_path:
        for r, o in roles.items():  # a role seen for the first time joins the baseline as it is
            base.setdefault(r, {"share": o["share"], "top3": list(o["rules"])[:3]})
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"baseline": base, "alerted": sorted(alerted | new),
                                          "at": int(time.time())}, indent=1), encoding="utf-8")
    if a.log and new:
        log = pathlib.Path(a.log)
        log.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M")
        with log.open("a", encoding="utf-8", newline="\n") as f:
            for k, t, role, rule, n, share in flags:
                if k in new:
                    hint = HINT.get(rule, "").replace('"', "'")
                    f.write(f"{stamp} TOOLUSE flag={k.split(':')[0]} role={role or '-'} rule={rule or '-'} n={n} "
                            f"share={share} hint=\"{hint}\"\n")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
