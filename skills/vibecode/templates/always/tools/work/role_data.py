"""The shared reading of the opencode database for the role tools: which role a session plays, what it cost at list
prices, and the week it falls in. Read-only, stdlib only (Windows and macOS).

Used by `tools/work/role-telemetry.py` (tool use, off-map reads, the rating) and `tools/work/roles.py`
(`may`: a role's spend this week). A session's role: its `agent` when that is a role (old names map: planner ->
product, cto -> architect, lane -> builder), else the brief it was started with (the first user message), else a tag
at the start of its title, else `other`. Its $: the database's `cost` when it has one, else its tokens at the list
prices of `tools/work/model-prices.json` (a subscription provider's session records no cost but is never $0),
else $0 for a local model; `kind` says which (`db`, `list`, `local`, `unknown`). Which project a session is:
`project_of` (`this` or `other`; budgets count `this`).

`python -I -X utf8 tools/work/role_data.py prices`: the running server's anthropic list prices beside the file's.
"""
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
PRICES = HERE / "model-prices.json"
ROLES = {"coordinator", "product", "architect", "plan-review", "pregroomer", "reviewer", "integrator", "librarian",
         "helper", "builder"}
AGENT_ROLES = ROLES
ALIASES = {"planner": "product", "cto": "architect", "pre-groomer": "pregroomer", "scout": "pregroomer",
           "plan-reviewer": "plan-review", "executor": "builder", "lane": "builder"}
BY_BRIEF = [  # the first user message (the commands and briefs); first match wins
    ("plan-review", r"workflow[\\/]plan-review\.md|You review one planning pass"),
    ("reviewer", r"reviewer-brief|You review one batch"),
    ("integrator", r"integrator-brief|You are the integrator"),
    ("builder", r"lane-brief|builder lane\b|You are the building session|You are (executor|lane)\b|"
                r"You(?: a|')re the builder\b|You are \W*\w+\W*, the builder\b"),
    ("pregroomer", r"workflow[\\/]pregroomer\.md|You are the pre-groomer"),
    ("architect", r"workflow[\\/](architect|cto)\.md|You are the (architect|CTO)\b"),
    ("librarian", r"workflow[\\/]librarian\.md|You are the librarian"),
    ("product", r"workflow[\\/]product\.md|docs[\\/]PLANNING\.md|You are the (product lead|planning session|planner)\b"),
    ("coordinator", r"workflow[\\/]coordinator\.md|coordinator-now|You are the coordinator"),
    ("helper", r"^Answer (this|these) question"),
]
BY_TITLE = [("plan-review", r"^(PLANREV|Plan review)"), ("reviewer", r"^(REV|Review)"),
            ("architect", r"^(ARCH|CTO)"), ("product", r"^(PROD|PLN|PLAN|Planner)"), ("pregroomer", r"^(PREGROOM|Pre-groom|SCOUT)"),
            ("librarian", r"^(LIB|Librarian)"), ("integrator", r"^(INT\d|Integrat)"), ("coordinator", r"[Cc]oordinator")]
COLS = ("id", "parent_id", "agent", "title", "directory", "time_created", "time_updated", "model", "cost",
        "tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write")


def role_of(agent, brief, title):
    agent = ALIASES.get((agent or "").lower(), (agent or "").lower())
    if agent in AGENT_ROLES:
        return agent
    for cut in (400, 3000):  # who it is, in its first lines, wins over a file the brief merely names
        for name, pat in BY_BRIEF:
            if brief and re.search(pat, brief[:cut], re.I | re.M):
                return name
    for name, pat in BY_TITLE:
        if re.search(pat, title or ""):
            return name
    return "other"


def default_db():
    base = os.environ.get("XDG_DATA_HOME") or str(pathlib.Path.home() / ".local" / "share")
    return pathlib.Path(base) / "opencode" / "opencode.db"


def main_work(root):
    """The main tree's `.work` for a checkout (a worktree's git common dir is the main tree's `.git`)."""
    root = pathlib.Path(root)
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel", "--git-common-dir"],
                             capture_output=True, text=True)
        lines = out.stdout.split("\n") if out.returncode == 0 else []
        top_ok = len(lines) > 1 and pathlib.Path(lines[0]).resolve() == root.resolve()  # not a folder inside a checkout
        common = pathlib.Path(lines[1].strip()) if top_ok and lines[1].strip() else None
    except OSError:
        common = None
    if common:
        common = common if common.is_absolute() else root / common
        if common.name == ".git":
            return common.resolve().parent / ".work"
    return root / ".work"


def connect(path=None):
    path = pathlib.Path(path or default_db())
    if not path.is_file():
        raise SystemExit(f"no opencode database at {path} (--db)")
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def load_prices(path=None):
    return json.loads(pathlib.Path(path or PRICES).read_text(encoding="utf-8"))


def model_of(raw):
    """(provider, model id, variant) of a session's `model` column (JSON or `provider/id#variant`)."""
    if not raw:
        return "", "", ""
    try:
        m = json.loads(raw)
        return m.get("providerID") or "", m.get("id") or "", m.get("variant") or ""
    except (ValueError, AttributeError, TypeError):
        prov, _, rest = str(raw).partition("/")
        mid, _, var = rest.partition("#")
        return prov, mid, var


def list_price(prices, provider, mid, tok):
    """$ of `tok` (input, output, reasoning, read, write) at list prices, or None when the model has none."""
    if provider in prices["local"]:
        return 0.0
    rid = prices["aliases"].get(mid, mid) if provider != "anthropic" else mid
    rate = (prices["models"].get(rid) or [None])[0]
    if not rate:
        return None
    i, o, r, cr, cw = tok
    return (i * rate["input"] + (o + r) * rate["output"] + cr * rate["read"] + cw * rate["write"]) / 1e6


def price_session(prices, provider, mid, cost, tok):
    if cost:
        return cost, "db"
    usd = list_price(prices, provider, mid, tok)
    if usd is None:
        return 0.0, "unknown"
    return usd, "local" if provider in prices["local"] else "list"


def week_start(now=None):
    """Monday 00:00 local time of the week holding `now` (epoch s), in epoch ms."""
    t = time.localtime(now or time.time())
    midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    return int((midnight - t.tm_wday * 86400) * 1000)


PROJECTS = ("this", "other")


def project_rules(path=None, name=None):
    """roles.json `projects`: `folder` and `word` (regexes; default from the main tree's folder name, so its
    `<name>-wt` worktrees count too), the spawn `preamble` cut out before `word` is looked for, the `exclude` list."""
    try:
        reg = json.loads(pathlib.Path(path or HERE / "roles.json").read_text(encoding="utf-8"))
        rules = reg.get("projects", {})
    except (OSError, ValueError):
        rules = {}
    name = re.escape(name or main_work(HERE.parents[1]).parent.name)
    return {"folder": re.compile(rules.get("folder", rf"[\\/]{name}(-wt)?([\\/]|$)"), re.I),
            "word": re.compile(rules.get("word", rf"\b{name}\b"), re.I),
            "preamble": re.compile(rules.get("preamble", rf"[^\s`'\"]*{name}[\\/]\.work[^\s`'\"]*"), re.I),
            "exclude": [(re.compile(e["title"], re.I), e["project"]) for e in rules.get("exclude", [])]}


def project_of(s, by_id=None, rules=None, depth=0):
    """`this` or `other`, decided in this order: the exclusion list (roles.json `projects.exclude`, by title); this
    project's folder (the main tree or a worktree); the parent's project; a brief naming the project once the spawn
    preamble (a path into the main tree's `.work`) is cut out; else `other`. A folder alone misses spawns that run
    in the home folder."""
    rules = rules or project_rules()
    title, folder = s.get("title") or "", s.get("directory") or ""
    for rx, proj in rules["exclude"]:  # an explicit line beats every heuristic below (the owner's overrule)
        if rx.search(title):
            return proj
    if rules["folder"].search(folder):
        return "this"
    parent = (by_id or {}).get(s.get("parent"))
    if parent and depth < 8 and project_of(parent, by_id, rules, depth + 1) == "this":
        return "this"
    if rules["word"].search(rules["preamble"].sub(" ", (s.get("brief") or "")[:3000])):
        return "this"
    return "other"


def sessions(db, since, until=None, project="", prices=None, scope="this"):
    """The sessions updated after `since` (ms): one dict each, with its role, project, model, tokens and $ (`usd`,
    split into `api_usd` paid and `list_usd` a subscription's tokens at list prices). `scope`: one project's
    sessions only (default this one's: budgets and `may`), None for all; `project`: a folder text filter."""
    prices = prices or load_prices()
    have = {r[1] for r in db.execute("pragma table_info(session_v2)")}
    pick = ", ".join(c if c in have else f"null as {c}" for c in COLS)
    rows = db.execute(f"select {pick} from session_v2 where time_updated > ?" + (" and time_updated <= ?" if until else ""),
                      (since, until) if until else (since,)).fetchall()
    out = {}
    for row in rows:
        s = dict(zip(COLS, row))
        if project and project.lower() not in (s["directory"] or "").lower():
            continue
        prov, mid, var = model_of(s["model"])
        tok = tuple(int(s[c] or 0) for c in COLS[9:])
        usd, kind = price_session(prices, prov, mid, s["cost"], tok)
        out[s["id"]] = {"id": s["id"], "parent": s["parent_id"], "agent": s["agent"], "title": s["title"] or "",
                        "created": s["time_created"] or s["time_updated"], "updated": s["time_updated"],
                        "model": f"{prov}/{mid}" + (f"#{var}" if var else "") if mid else "", "tokens": sum(tok),
                        "tok": tok, "usd": round(usd, 4), "kind": kind, "brief": None, "directory": s["directory"],
                        "api_usd": round(usd, 4) if kind == "db" else 0.0,
                        "list_usd": round(usd, 4) if kind == "list" else 0.0}
    for sid, s in out.items():
        s["brief"] = first_user(db, sid)
    by_id, todo = dict(out), {s["parent"] for s in out.values() if s["parent"]}
    while todo:  # parents outside the window still decide their children's project
        pid = todo.pop()
        if pid in by_id:
            continue
        r = db.execute("select title, directory, parent_id from session_v2 where id = ?", (pid,)).fetchone() \
            if "parent_id" in have else None
        if r:
            by_id[pid] = {"id": pid, "title": r[0], "directory": r[1], "parent": r[2], "brief": first_user(db, pid)}
            if r[2]:
                todo.add(r[2])
    rules = project_rules()
    for s in out.values():
        s["role"] = role_of(s["agent"], s["brief"], s["title"])
        s["project"] = project_of(s, by_id, rules)
    return {k: s for k, s in out.items() if s["project"] == scope} if scope else out


def first_user(db, sid):
    """A session's brief: its first user message, else its first text part (the brief some clients send that way)."""
    for q in ("select data from session_message where session_id = ? and type = 'user' order by seq limit 1",
              "select data from session_part where session_id = ? and type = 'text' order by time_created limit 1"):
        try:
            r = db.execute(q, (sid,)).fetchone()
            text = json.loads(r[0]).get("text") if r else None
        except (ValueError, AttributeError, sqlite3.OperationalError):
            text = None
        if text:
            return text
    return None


def message_spend(db, since, ids, prices=None, by_week=False):
    """{session: {"api": $, "list": $}} (or {session: {week: ...}}) from its own requests after `since` (ms), so a
    session started before Monday counts only this week's part; each request priced by its own model."""
    prices = prices or load_prices()
    out = {}
    have = {r[1] for r in db.execute("pragma table_info(session_message)")}
    if "time_created" not in have:
        return out
    for sid, created, data in db.execute("select session_id, time_created, data from session_message where type = "
                                         "'assistant' and time_created >= ?", (since,)):
        if sid not in ids:
            continue
        try:
            d = json.loads(data)
        except ValueError:
            continue
        m, t = d.get("model") or {}, d.get("tokens") or {}
        c = t.get("cache") or {}
        tok = (t.get("input") or 0, t.get("output") or 0, t.get("reasoning") or 0, c.get("read") or 0, c.get("write") or 0)
        usd, kind = price_session(prices, m.get("providerID") or "", m.get("id") or "", d.get("cost"), tok)
        if kind == "unknown":
            continue
        slot = out.setdefault(sid, {})
        if by_week:
            slot = slot.setdefault(time.strftime("%G-W%V", time.localtime(created / 1000)), {})
        slot["api" if kind == "db" else "list"] = slot.get("api" if kind == "db" else "list", 0.0) + usd
    return out


def first_steps(db, ids):
    """Each session's first request in tokens (input + cache read + cache write): the fixed cost of its setup."""
    got = {}
    for sid in ids:
        r = db.execute("select data from session_message where session_id = ? and type = 'assistant' order by seq limit 1",
                       (sid,)).fetchone()
        try:
            t = json.loads(r[0]).get("tokens") or {} if r else {}
        except (ValueError, AttributeError):
            t = {}
        c = t.get("cache") or {}
        got[sid] = (t.get("input") or 0) + (c.get("read") or 0) + (c.get("write") or 0)
    return got


def project_spend(db, since=None, prices=None):
    """{project: {"runs", "api", "list"}} for every session active since `since` (default: this week): all spend in
    one place, this project's beside the others."""
    since = since or week_start()
    ss = sessions(db, since, prices=prices, scope=None)
    spent = message_spend(db, since, set(ss), prices)
    out = {p: {"runs": 0, "api": 0.0, "list": 0.0} for p in PROJECTS}
    for sid, s in ss.items():
        o = out.setdefault(s["project"], {"runs": 0, "api": 0.0, "list": 0.0})
        o["runs"] += 1
        sp = spent.get(sid) or ({"api": s["api_usd"], "list": s["list_usd"]} if (s["created"] or 0) >= since else {})
        o["api"] += sp.get("api", 0.0)
        o["list"] += sp.get("list", 0.0)
    return out


def week_spend(db, since=None, prices=None):
    """{role: {"runs", "api", "list"}}: this project's sessions active since `since` (default: this week), their $ from
    the requests made since then. The one reader `roles.py show`/`may` and the rating share."""
    since = since or week_start()
    ss = sessions(db, since, prices=prices)
    spent = message_spend(db, since, set(ss), prices)
    out = {}
    for sid, s in ss.items():
        o = out.setdefault(s["role"], {"runs": 0, "api": 0.0, "list": 0.0})
        o["runs"] += 1
        sp = spent.get(sid)
        if not sp and s["created"] and s["created"] >= since:  # no priced per-request rows: the session's own total
            sp = {"api": s["api_usd"], "list": s["list_usd"]}
        o["api"] += (sp or {}).get("api", 0.0)
        o["list"] += (sp or {}).get("list", 0.0)
    return out


def main(argv):
    if argv[:1] != ["prices"]:
        print(__doc__)
        return 2
    exe = "opencode.cmd" if os.name == "nt" else "opencode"
    out = subprocess.run([exe, "api", "model.list"], capture_output=True, text=True, encoding="utf-8").stdout
    live = json.JSONDecoder().raw_decode(out[out.find("["):])[0] if "[" in out else []
    have = load_prices()["models"]
    for m in live:
        if m.get("providerID") == "anthropic" and m.get("cost"):
            c = m["cost"][0]
            now = {"input": c["input"], "output": c["output"], "read": c["cache"]["read"], "write": c["cache"]["write"]}
            mark = "same" if have.get(m["id"], [{}])[0] == now else "CHANGED" if m["id"] in have else "new"
            print(f"{mark:8} {m['id']:28} {now}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
