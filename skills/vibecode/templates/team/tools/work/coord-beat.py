#!/usr/bin/env python3
"""The coordinator's heartbeat in at most 25 lines: `python -I -X utf8 tools/work/coord-beat.py [--parent ses_..]`.

One call instead of five: the pacer's usage line (`pacer.py status --json` from the opt-in kit, skipped when missing), the latest
gate verdict per sha (`.work/ci/<sha8>.json` and `.only.json`, and a run folder with no verdict yet), the machine
locks (`.work/<name>.lock` or `.lock.d`: holder and age; a running or failed autodeploy too), the gates waiting for a
lock or a CPU slot (the `waiting for` lines of each run's `gate.log`; not a gate whose `gate.pid` is gone), the coordinator's child sessions with how long each has
been quiet (the opencode database read-only for the children; with no database, `opencode session list --format
json` for the top level), the head of `.work/integrator-queue.md`, and a nudge from `.work/kmap/review.json`.

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


def pacer() -> list[str]:
    """The pacer's usage line from `pacer.py status --json` (it writes nothing): `$PACER_PY` when set, else the
    opt-in kit's install, `<config>/opencode/plugins/pacer/pacer.py`. No pacer: no line."""
    conf = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode" / "plugins" / "pacer"
    script = Path(os.environ.get("PACER_PY") or conf / "pacer.py")
    if not script.is_file():
        return []
    out = run([sys.executable, "-I", str(script), "status", "--json"], 30).strip().splitlines()
    try:
        d = json.loads(out[-1])
    except (IndexError, ValueError):
        d = {}
    return [str(d["line"])[:200]] if d.get("ok") and d.get("line") else ["PACE ? (no usage reading: pacer.py failed or no valid token)"]


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
    parts = deploy(work / "deploy", now_ms)
    if not found and not parts:
        return ["locks: none held"]
    for p in found:
        src = p / "who" if p.is_dir() else p
        try:
            text = src.read_text(encoding="utf-8", errors="replace").split()
        except OSError:
            text = []
        parts.append(f"{p.name} {text[0] if text else '?'} {ago(p.stat().st_mtime * 1000, now_ms)}")
    return ["locks: " + " | ".join(parts)]


WAIT_MIN = 240  # a gate waits at most `wait_minutes` (180) per part: an older gate.log is no live wait
SAID = re.compile(r"([\w.-]+): waiting for (.+)")


def gone(run: Path) -> bool:
    """A run whose gate died: its `gate.pid` names a process that is gone, or one started after the file was written
    (the pid reused). A killed gate leaves its `waiting for` lines; they are no waits. No `gate.pid`: not known gone."""
    try:
        f = run / "gate.pid"
        pid, written = re.search(r"^pid (\d+)$", f.read_text(encoding="utf-8"), re.M), f.stat().st_mtime
    except OSError:
        return False
    if not pid:
        return False
    pid = int(pid[1])
    if os.name != "nt":
        try:
            os.kill(pid, 0)  # signal 0: a check, not a signal (on Windows it would end the process)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() != 5  # access denied: it is there
    code, times = ctypes.c_ulong(), [wintypes.FILETIME() for _ in range(4)]
    try:
        k32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        timed = k32.GetProcessTimes(ctypes.c_void_p(handle), *(ctypes.byref(t) for t in times))
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))
    born = ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime) / 1e7 - 11644473600 if timed else None
    return code.value != 259 or (born is not None and born > written + 2)  # 259: STILL_ACTIVE; 2 s: the clocks' grain


def waits(ci: Path, now_ms: float) -> list[tuple[str, str, str, float]]:
    """(run, part, what, when said) for each part of a running gate whose last word is a `waiting for` line in its
    `gate.log`: no `$ <part>` (its command started) since, no verdict written since, the log under WAIT_MIN old. Only
    the gate's own log counts: a run on another machine, or a hand run, is no local waiter."""
    rows = []
    for d in sorted(ci.iterdir()) if ci.is_dir() else []:
        log = d / "gate.log"
        if not (d.is_dir() and re.fullmatch(r"[0-9a-f]{8}(\.only)?", d.name) and log.is_file()):
            continue
        t, verdict = log.stat().st_mtime, ci / f"{d.name}.json"
        if now_ms / 1000 - t > WAIT_MIN * 60 or (verdict.is_file() and verdict.stat().st_mtime >= t) or gone(d):
            continue
        said: dict[str, str] = {}
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if m := SAID.fullmatch(line.strip()):
                said[m[1]] = re.sub(r" since \S+", "", m[2])
            elif line.startswith("$ "):
                said.pop(line[2:].strip(), None)
        rows += [(d.name, k, v, t) for k, v in said.items()]
    return rows


def waiting(ci: Path, now_ms: float) -> list[str]:
    """`waiting:` each waiting part (`waits`) with how long ago it said so; or `waiting: none`."""
    rows = [f"{run} {part}: {what} ({ago(t * 1000, now_ms)})" for run, part, what, t in waits(ci, now_ms)]
    return ["waiting: " + (" | ".join(rows) if rows else "none")]


def deploy(d: Path, now_ms: float) -> list[str]:
    """Autodeploy (`tools/ci/autodeploy.py`, opt-in): a running deploy and a failed last one; nothing when quiet."""
    out = []
    if (d / "run.lock").is_file():
        out.append(f"deploy running {ago((d / 'run.lock').stat().st_mtime * 1000, now_ms)}")
    try:
        s = json.loads((d / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    if s.get("word") == "FAIL":
        out.append(f"deploy FAIL {str(s.get('head', '?'))[:8]} at {str(s.get('at', '?'))[11:16]} "
                   f"(tools/ci/autodeploy.py status)")
    return out


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


def review(work: Path, now_ms: float) -> list[str]:  # kmap_review.write_due; `size`: the log's bytes at its count
    try:
        r = json.loads((work / "kmap" / "review.json").read_text(encoding="utf-8"))
        n, since = r["unreviewed"], r["since"] * 1000 or now_ms
        c, due = work / "kmap" / "candidates.jsonl", n > r["max"] or (n and now_ms - since >= r["days"] * DAY_MS)
        n = f"{n}+" if c.is_file() and c.stat().st_size != r.get("size") else n      # lines appended since its count
        return [f"kmap: {n} candidate links to review ({ago(since, now_ms)}): `kmap.py candidates --review`"] if due else []
    except (OSError, ValueError, KeyError, TypeError):
        return []


def beat(work: Path, parent: str | None, now_ms: float, top=None, db=None) -> list[str]:
    out = pacer() + review(work, now_ms) + gates(work / "ci", now_ms) + locks(work, now_ms)
    out += waiting(work / "ci", now_ms)
    out += sessions(parent, now_ms, top, db) + queue(work / "integrator-queue.md")
    return out[:MAX_LINES]


def selftest() -> int:
    now = time.time() * 1000
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / ".work"
        (work / "ci" / "deadbeef").mkdir(parents=True)
        (work / "ci" / "deadbeef" / "unit.log").write_text("x", encoding="utf-8")
        (work / "ci" / "deadbeef" / "gate.log").write_text(
            "e2e: waiting for e2e or e2e2, held by ci-x since 1 and ci-y since 2\nunit: waiting for 3 of 3 cpu slots\n"
            "$ unit\n", encoding="utf-8")
        (work / "ci" / "deadbeef" / "gate.pid").write_text(f"pid {os.getpid()}\n", encoding="utf-8")  # alive: kept
        dead = Path(tmp) / "w2" / "ci" / "0badc0de"  # a killed gate: its pid is gone (no real pid is that odd high)
        dead.mkdir(parents=True)
        (dead / "gate.log").write_text("unit: waiting for 1 of 1 cpu slots\n", encoding="utf-8")
        (dead / "gate.pid").write_text("pid 4194303\nsession ses_x\n", encoding="utf-8")
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
        (work / "deploy").mkdir()
        (work / "deploy" / "state.json").write_text('{"word": "FAIL", "head": "cafef00d99", "at": "2026-11-24 13:05:00"}',
                                                    encoding="utf-8")
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
        fake = Path(tmp) / "fake_pacer.py"
        fake.write_text('print(\'{"ok": true, "line": "PACE 12:00 | mode GO lanes<=5"}\')\n', encoding="utf-8")
        before = sorted((p, p.stat().st_mtime) for p in Path(tmp).rglob("*"))
        top = [{"id": "ses_boss", "title": "project coordinator", "updated": now}]
        os.environ.pop("OPENCODE_SESSION_ID", None)
        os.environ["PACER_PY"] = str(Path(tmp) / "none.py")
        out = beat(work, None, now, top, db)
        flat = "\n".join(out)
        os.environ["PACER_PY"] = str(fake)
        paced = pacer()
        checks = {
            "at most 25 lines": len(out) <= MAX_LINES,
            "no pacer, no line": not flat.startswith("PACE"),
            "pacer's usage line": paced == ["PACE 12:00 | mode GO lanes<=5"],
            "newest five gates": "gates (newest 5)" in flat and "00000003" in flat and "00000004" not in flat,
            "a running gate": "deadbeef RUNNING?" in flat,
            "a new red named": "00000000 NEW-RED" in flat and "a.spec.ts" in flat,
            "full verdict over --only": "00000001 GREEN" in flat and "--only" not in flat,
            "locks": "e2e.lock RV16" in flat and "perf.lock.d LANE2" in flat,
            "a gate waiting": "waiting: deadbeef e2e: e2e or e2e2, held by ci-x and ci-y (0m)" in flat,
            "a started part no longer waits": "cpu slots" not in flat,
            "a dead gate's waits dropped": waits(dead.parent, now) == [],
            "deploy FAIL in locks": "deploy FAIL cafef00d at 13:05" in flat,
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
