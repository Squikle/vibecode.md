#!/usr/bin/env python3
"""The coordinator's heartbeat in at most 25 lines: `python -I -X utf8 tools/work/coord-beat.py [--parent ses_..]`.

One call instead of five: the pacer's first line (`.work/pacer/pacer.py status`, skipped when missing), the latest
gate verdict per sha (`.work/ci/<sha8>.json` and `.only.json`, and a run folder with no verdict yet), the machine
locks (`.work/<name>.lock` or `.lock.d`: holder and age), the coordinator's child sessions with how long each has
been quiet (the opencode database read-only for the children; with no database, `opencode session list --format
json` for the top level), and the head of `.work/integrator-queue.md`.

Read-only: it writes nothing, takes no lock and starts nothing but `git`, the pacer and `opencode session list`.
The standard library only, so it runs the same on every OS. `--selftest` checks it on a scratch `.work`.
The coordinator is `--parent`, else `$OPENCODE_SESSION_ID` when that session has children, else the newest top-level
session titled "coordinator". Unknown arguments are an error."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_LINES = 25
GATES, KIDS = 5, 8  # gate rows, child rows
DAY_MS = 24 * 3600 * 1000
RUNNING_S = 3600  # a run folder with no verdict counts as running this long
USAGE = "usage: coord-beat.py [--parent ses_<id>] [--work <main tree>/.work] [--selftest]"


def run(cmd: list[str], timeout: int = 60) -> str:
    """A command's stdout, or "" when it is missing, fails or hangs."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def main_work() -> Path:
    """`<main tree>/.work`: the parent of the git common dir of this script's checkout."""
    common = run(["git", "-C", str(HERE), "rev-parse", "--path-format=absolute", "--git-common-dir"], 20).strip()
    return (Path(common).parent if common else HERE.parents[1]) / ".work"


def ago(ms_then: float, now_ms: float) -> str:
    m = max(0, int((now_ms - ms_then) / 60000))
    return f"{m}m" if m < 120 else f"{m // 60}h" if m < 2880 else f"{m // 1440}d"


def pacer(work: Path) -> list[str]:
    script = work / "pacer" / "pacer.py"
    if not script.is_file():
        return []
    first = run([sys.executable, "-I", str(script), "status"], 30).strip().splitlines()[:1]
    return first or ["PACE ? (pacer.py status printed nothing)"]


def red_name(red) -> str:
    """A new red as `file::test`, cut short: the gate writes a dict with its `id` (or a plain name)."""
    name = str(red.get("id") or red.get("name") or red) if isinstance(red, dict) else str(red)
    return name.rsplit("/", 1)[-1][:48]


def gates(ci: Path, now_ms: float) -> list[str]:
    """The newest GATES shas: the full run's verdict (else the `--only` one), its age, its new reds."""
    if not ci.is_dir():
        return [f"gates: no {ci}"]
    runs: dict[str, dict] = {}
    for f in ci.glob("*.json"):
        m = re.fullmatch(r"([0-9a-f]{8})(\.only)?\.json", f.name)
        if not m:
            continue
        r = runs.setdefault(m[1], {"t": 0.0})
        r["t"] = max(r["t"], f.stat().st_mtime)
        r["only" if m[2] else "full"] = f
    for d in ci.iterdir():
        if d.is_dir() and re.fullmatch(r"[0-9a-f]{8}", d.name) and d.name not in runs:
            t = max((p.stat().st_mtime for p in d.iterdir()), default=d.stat().st_mtime)
            if now_ms / 1000 - t < RUNNING_S:
                runs[d.name] = {"t": t, "running": True}
    rows = []
    for sha, r in sorted(runs.items(), key=lambda kv: -kv[1]["t"])[:GATES]:
        if r.get("running"):
            rows.append(f"  {sha} RUNNING? (no verdict yet, logs {ago(r['t'] * 1000, now_ms)} old)")
            continue
        f = r.get("full") or r["only"]
        try:
            g = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows.append(f"  {sha} UNREADABLE {f.name}")
            continue
        new = sorted({red_name(n) for p in g.get("parts", []) for n in p.get("new", [])})
        tail = f" new: {', '.join(new[:2])}{' +%d' % (len(new) - 2) if len(new) > 2 else ''}" if new else ""
        kind = "" if "full" in r else " (--only)"
        shipped = " shipped" if g.get("shipped") else ""
        rows.append(f"  {sha} {g.get('verdict', '?')}{kind}{shipped} {ago(r['t'] * 1000, now_ms)} ago{tail}")
    return [f"gates (newest {len(rows)}):"] + rows if rows else ["gates: none yet"]


def locks(work: Path, now_ms: float) -> list[str]:
    """Each machine lock: `<name>.lock` (a file, lock.ps1) or `<name>.lock.d` (a folder with `who`, lock.sh)."""
    found = sorted(p for p in work.glob("*.lock*") if re.fullmatch(r"[\w.-]+\.lock(\.d)?", p.name)) \
        if work.is_dir() else []
    if not found:
        return ["locks: none held"]
    parts = []
    for p in found:
        src = p / "who" if p.is_dir() else p
        try:
            text = src.read_text(encoding="utf-8", errors="replace").split()
        except OSError:
            text = []
        parts.append(f"{p.name} {text[0] if text else '?'} {ago(p.stat().st_mtime * 1000, now_ms)}")
    return ["locks: " + " | ".join(parts)]


def top_sessions() -> list[dict]:
    try:
        rows = json.loads(run(["opencode", "session", "list", "--format", "json", "-n", "40"]) or "[]")
    except ValueError:
        return []
    return [r for r in rows if isinstance(r, dict) and "id" in r]


def opencode_db() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "opencode" / "opencode.db"


def children(db: Path, parent: str) -> list[tuple] | None:
    """(id, title, updated, busy, outcome) of `parent`'s children, newest first; None when the db can't say
    (no file, another schema: the caller falls back to the CLI list)."""
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute("select id, title, time_updated, time_busy, time_idle, idle_outcome from session_v2 "
                               "where parent_id = ? order by time_updated desc limit 40", (parent,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return [(i, t or "", u or 0, bool(b) and (not idle or b > idle), o) for i, t, u, b, idle, o in rows]


def pick_parent(arg: str | None, top: list[dict], db: Path) -> str | None:
    if arg:
        return arg
    env = os.environ.get("OPENCODE_SESSION_ID")
    if env and children(db, env):
        return env
    coords = [s for s in top if "coordinator" in str(s.get("title", "")).lower()]
    return max(coords, key=lambda s: s.get("updated", 0))["id"] if coords else None


def sessions(parent_arg: str | None, now_ms: float, top: list[dict] | None = None, db: Path | None = None) -> list[str]:
    top = top_sessions() if top is None else top
    db = opencode_db() if db is None else db
    parent = pick_parent(parent_arg, top, db)
    kids = children(db, parent) if parent else None
    if kids is None:  # no database: the top level only, as the CLI lists it
        recent = [s for s in top if now_ms - s.get("updated", 0) < DAY_MS][:KIDS]
        if not recent:
            return ["sessions: none in the last day" if top else "sessions: opencode gave no list"]
        return ["sessions (top level, quiet for):"] + [
            f"  {s['id'][-8:]} {ago(s.get('updated', 0), now_ms):>4} {str(s.get('title', ''))[:60]}" for s in recent]
    live = [k for k in kids if k[3]] + [k for k in kids if not k[3] and now_ms - k[2] < DAY_MS]
    if not live:
        return [f"children of {parent[-8:]}: none in the last day"]
    rows = [f"  {i[-8:]} {'BUSY' if busy else (o or 'idle')[:9]:>9} quiet {ago(u, now_ms):>4} {t[:52]}"
            for i, t, u, busy, o in live[:KIDS]]
    more = f" (+{len(live) - KIDS} more)" if len(live) > KIDS else ""
    return [f"children of {parent[-8:]} ({sum(k[3] for k in kids)} busy){more}:"] + rows


def queue(path: Path) -> list[str]:
    """The `## Next job` section's first lines and the `## Last job` line; else the file's first lines."""
    if not path.is_file():
        return [f"queue: no {path.name}"]
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    sections: dict[str, list[str]] = {}
    cur = None
    for ln in lines:
        if ln.startswith("## "):
            cur = ln[3:].strip().lower()
            sections[cur] = []
        elif cur is not None and ln.strip():
            sections[cur].append(ln.strip())
    nxt, last = sections.get("next job"), sections.get("last job")
    if nxt is None and last is None:
        return ["queue:"] + ["  " + ln[:110] for ln in lines if ln.strip()][:4]
    out = ["queue next: " + ((nxt or ["(none)"])[0][:100])] + ["  " + ln[:110] for ln in (nxt or [])[1:3]]
    return out + ["queue last: " + ((last or ["(none)"])[0][:100])]


def beat(work: Path, parent: str | None, now_ms: float, top=None, db=None) -> list[str]:
    out = pacer(work) + gates(work / "ci", now_ms) + locks(work, now_ms)
    out += sessions(parent, now_ms, top, db) + queue(work / "integrator-queue.md")
    return out[:MAX_LINES]


def selftest() -> int:
    now = time.time() * 1000
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / ".work"
        (work / "ci" / "deadbeef").mkdir(parents=True)
        (work / "ci" / "deadbeef" / "unit.log").write_text("x", encoding="utf-8")
        for i in range(7):
            g = {"verdict": "GREEN" if i else "NEW-RED", "parts": [{"new": [{"id": "e2e/a.spec.ts::b"}] if i == 0 else []}]}
            f = work / "ci" / f"{i:08x}.json"
            f.write_text(json.dumps(g), encoding="utf-8")
            os.utime(f, (now / 1000 - 60 * (i + 1),) * 2)
        f = work / "ci" / "00000001.only.json"
        f.write_text('{"verdict": "KNOWN-RED"}', encoding="utf-8")
        os.utime(f, (now / 1000 - 600,) * 2)
        (work / "e2e.lock").write_text("RV16 2026-11-24T12:19:22-05:00", encoding="utf-8")
        (work / "perf.lock.d").mkdir()
        (work / "perf.lock.d" / "who").write_text("LANE2 2026-11-24", encoding="utf-8")
        (work / "integrator-queue.md").write_text("# q\n\nrules\n\n## Next job\n\nmerge x\n\n## Last job\n\nINT9 ok\n",
                                                 encoding="utf-8")
        db = Path(tmp) / "oc.db"
        con = sqlite3.connect(db)
        con.execute("create table session_v2 (id, parent_id, title, time_updated, time_busy, time_idle, idle_outcome)")
        kids = [("ses_kid1", "ses_boss", "LANE1 build", now - 60000, now - 9e5, None, None),
                ("ses_kid2", "ses_boss", "RV1 review", now - 3e6, now - 9e6, now - 3e6, "succeeded"),
                ("ses_old", "ses_boss", "OLD", now - 3 * DAY_MS, 1, 2, "succeeded")]
        con.executemany("insert into session_v2 values (?,?,?,?,?,?,?)", kids)
        con.commit()
        con.close()
        before = sorted((p, p.stat().st_mtime) for p in Path(tmp).rglob("*"))
        top = [{"id": "ses_boss", "title": "project coordinator", "updated": now}]
        os.environ.pop("OPENCODE_SESSION_ID", None)
        out = beat(work, None, now, top, db)
        flat = "\n".join(out)
        checks = {
            "at most 25 lines": len(out) <= MAX_LINES,
            "no pacer, no line": not flat.startswith("PACE"),
            "newest five gates": "gates (newest 5)" in flat and "00000003" in flat and "00000004" not in flat,
            "a running gate": "deadbeef RUNNING?" in flat,
            "a new red named": "00000000 NEW-RED" in flat and "a.spec.ts" in flat,
            "full verdict over --only": "00000001 GREEN" in flat and "--only" not in flat,
            "locks": "e2e.lock RV16" in flat and "perf.lock.d LANE2" in flat,
            "busy child": "BUSY" in flat and "LANE1 build" in flat,
            "idle child with outcome": "succeeded" in flat and "RV1 review" in flat,
            "old child dropped": "OLD" not in flat,
            "queue next and last": "queue next: merge x" in flat and "queue last: INT9 ok" in flat,
            "read-only": before == sorted((p, p.stat().st_mtime) for p in Path(tmp).rglob("*")),
            "no db: top level": "top level" in "\n".join(sessions(None, now, top, Path(tmp) / "none.db")),
            "no locks": locks(Path(tmp) / "nowhere", now) == ["locks: none held"],
            "unknown arg refused": main(["--bogus"]) == 2 and main(["x"]) == 2,
        }
    bad = [k for k, ok in checks.items() if not ok]
    if bad:
        print(flat)
    print(f"SELFTEST {'FAIL: ' + '; '.join(bad) if bad else 'OK'} ({len(checks)} checks)")
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    opts, it = {}, iter(argv)
    for a in it:
        if a == "--selftest" and not opts:
            opts["selftest"] = True
        elif a in ("--parent", "--work") and a not in opts:
            opts[a] = next(it, None)
            if not opts[a] or opts[a].startswith("--"):
                print(f"{a} needs a value\n{USAGE}", file=sys.stderr)
                return 2
        else:
            print(f"unknown argument: {a}\n{USAGE}", file=sys.stderr)
            return 2
    if opts.get("selftest"):
        if len(opts) > 1:
            print(USAGE, file=sys.stderr)
            return 2
        return selftest()
    work = Path(opts["--work"]) if "--work" in opts else main_work()
    print("\n".join(beat(work, opts.get("--parent"), time.time() * 1000)))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
