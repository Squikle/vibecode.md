#!/usr/bin/env python3
"""The local CI gate (`tools/ci/README.md`): one call per sha, one status line. Stack-agnostic: `tools/ci/ci.json`
names the parts (test kinds), their commands, the paths that trigger each and where its JUnit report lands.

    python tools/ci/gate.py plan [sha] [--base origin/main] [--parts a b] [--no-remote]   what would run, and why
    python tools/ci/gate.py run [sha] [same options] [--keep]       the gate: `.work/ci/<sha8>.json` + one line
    python tools/ci/gate.py run [sha] --only unit tests/test_x.py   quiet (<= 15 lines), never shippable
    python tools/ci/gate.py run [sha] ... --stack 2                 each locked part on its 2nd lock (a `lock` list)
    python tools/ci/gate.py status [sha]                            the last run's line again
    python tools/ci/gate.py ship <sha> --batch <id> --what .. --try .. [--up ..] [--no-roadmap] [--dry-run]

Verdicts: GREEN, KNOWN-RED (every red listed in known-reds.json, or green alone 2 of 2; 3 of 3 for a listed flake),
NEW-RED, BUSY (a heavy part skipped: the machine is busy), WAITED (a lock or a CPU slot stayed taken `wait_minutes`:
nothing ran), ERROR (the gate or a runner broke). Exit 0 only for the
first two. Stdlib only; Windows, macOS and Linux. Unknown arguments are an error (argparse), never guessed."""
import argparse
import ctypes
import datetime
import fnmatch
import glob
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if WINDOWS else 0
QUIET_LINES = 15
SHIPPABLE = ("GREEN", "KNOWN-RED")
VERDICTS = ("GREEN", "KNOWN-RED", "BUSY", "WAITED", "NEW-RED", "ERROR")  # the worst found wins
DEFAULTS = {"base": "origin/main", "lock_minutes": 50, "wait_minutes": 180, "parts": [], "busy": {"processes": []},
            "docs_only": ["docs/*", "*.md"], "ship": {}, "cpu_slots": 3, "cpu_max": 90, "software_slots": 1}
LOG_TAIL = 40  # lines of a failed part's log kept in its error


# ---------------------------------------------------------------- git, config, processes

def run(args, cwd=None, env=None, timeout=None, log=None, shell=False) -> subprocess.CompletedProcess:
    """A command; its output captured (or written to `log`). On timeout the whole process tree is killed."""
    out = open(log, "wb") if log else subprocess.PIPE
    try:
        p = subprocess.Popen(args, cwd=cwd, env=env, shell=shell, stdout=out, stderr=subprocess.STDOUT if log else subprocess.PIPE,
                             creationflags=NO_WINDOW, start_new_session=not WINDOWS)
        try:
            so, se = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(p)
            p.communicate()
            return subprocess.CompletedProcess(args, -9, "", "timed out")
        dec = lambda b: b.decode("utf-8", "replace") if isinstance(b, bytes) else (b or "")
        return subprocess.CompletedProcess(args, p.returncode, dec(so), dec(se))
    finally:
        if log:
            out.close()


def kill_tree(p) -> None:
    if WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, creationflags=NO_WINDOW)
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            p.kill()


def git(*args, cwd=None) -> str:
    done = run(["git", *args], cwd=cwd)
    if done.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {(done.stderr or done.stdout).strip()}")
    return done.stdout.strip()


def main_tree() -> Path:
    """The main checkout: the folder that holds the shared `.git` (its `.work/` holds results, logs and locks)."""
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=HERE)
    return Path(common).parent


def load(path: str | None) -> dict:
    """`tools/ci/ci.json` with this machine's `.work/ci/machine.json` (git-ignored, or `--config`) merged over it."""
    config = {**DEFAULTS, **json.loads((HERE / "ci.json").read_text(encoding="utf-8"))}
    local = Path(path) if path else main_tree() / ".work" / "ci" / "machine.json"
    if local.exists():
        for k, v in json.loads(local.read_text(encoding="utf-8")).items():
            config[k] = {**config[k], **v} if isinstance(v, dict) and isinstance(config.get(k), dict) else v
    config.setdefault("name", platform.node().split(".")[0].lower() or "this")
    config["main"] = str(main_tree())
    return config


def out_dir(config: dict) -> Path:
    return Path(config["main"]) / ".work" / "ci"


def matches(path: str, globs: list[str]) -> bool:
    """fnmatch on the posix path: `*` crosses folders, so `web/*` is everything under web/."""
    return any(g == "*" or fnmatch.fnmatch(path, g) for g in globs)


# ---------------------------------------------------------------- plan

def changed_files(sha: str, base: str) -> list[str]:
    return git("diff", "--name-only", f"{git('merge-base', base, sha)}", sha).split()


def build_plan(config: dict, sha: str, base: str, picked: list[str] | None, only: list[str] | None,
               stack: int | None = None) -> dict:
    """The parts the diff (or `--parts`, `--only`) runs. A locked part's `try_locks`: `--stack N` its Nth lock (a part
    with fewer keeps its first); else all of them, the first free taken (a full gate too: a free stack never idles
    while a gate waits for a busy one)."""
    parts, skipped, why = [], [], {}
    known = {p["name"]: p for p in config["parts"]}
    if only:
        files: dict[str, list[str]] = {}
        for item in only:
            if item in known:
                files.setdefault(item, [])
                continue
            item = item.replace("\\", "/")
            bare = re.sub(r":\d+$", "", item)  # `tests/x.spec.ts:115` (one test by its line): match the file, pass as given
            owner = next((p for p in config["parts"] if p.get("files_cmd") and matches(bare, p.get("match", []))), None)
            if not owner:
                raise SystemExit(f"--only: not a part and no part's `match` takes this file: {item} "
                                 f"(parts: {', '.join(known)})")
            files.setdefault(owner["name"], []).append(item)
        for name, fs in files.items():
            parts.append({**known[name], "files": fs})
            why[name] = "--only"
        mode, changed = "only", []
    else:
        changed = changed_files(sha, base)
        for p in config["parts"]:
            if picked is not None:
                if p["name"] in picked:
                    parts.append(dict(p))
                    why[p["name"]] = "--parts"
                continue
            hit = [f for f in changed if matches(f, p.get("paths", ["*"]))]
            if hit:
                parts.append(dict(p))
                why[p["name"]] = f"{len(hit)} file(s), e.g. {hit[0]}"
            else:
                skipped.append({"name": p["name"], "off": True, "reason": "no file of its paths changed"})
        if picked:
            unknown = sorted(set(picked) - set(known))
            if unknown:
                raise SystemExit(f"--parts: unknown part(s) {', '.join(unknown)} (parts: {', '.join(known)})")
        mode = "parts" if picked is not None else "auto"
    for p in list(parts):
        if p.get("remote") and (config.get("no_remote") or not config.get("remote")):
            parts.remove(p)
            skipped.append({"name": p["name"], "off": True, "reason": "--no-remote" if config.get("no_remote") else "no remote in the config"})
    for p in parts:
        if p.get("lock"):
            p["locks"] = [p["lock"]] if isinstance(p["lock"], str) else list(p["lock"])
            n = len(p["locks"])
            p["try_locks"] = [p["locks"][stack - 1]] if stack and n >= stack else p["locks"][:1] if stack else p["locks"]
    if stack and not any(len(p.get("locks", [])) >= stack for p in parts):
        raise SystemExit(f"--stack {stack}: no part of this run has a lock {stack} (a `lock` list in tools/ci/ci.json); "
                         "run without --stack")
    return {"sha": sha, "base": base, "mode": mode, "files": len(changed), "parts": parts, "why": why, "skipped": skipped,
            "options": {"no_remote": bool(config.get("no_remote")), **({"stack": stack} if stack else {})}}


def describe(plan: dict) -> str:
    lines = [f"PLAN {plan['sha'][:8]} ({plan['mode']}, {plan['files']} files vs {plan['base'] or '-'})"]
    for p in plan["parts"]:
        where = "remote" if p.get("remote") else "docker" if p.get("docker") else "host"
        extra = f" files={' '.join(p['files'])}" if p.get("files") else ""
        extra += f" lock={' or '.join(p['try_locks'])}" if p.get("try_locks") else ""
        lines.append(f"  run  {p['name']} [{where}]{extra}: {plan['why'].get(p['name'], '')}")
    for s in plan["skipped"]:
        lines.append(f"  skip {s['name']}: {s['reason']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- locks and the busy guard

WAIT_S = 10  # between two tries at a held lock, as `tools/work/lock.ps1` and `lock.sh`
TOUCH_S = 60  # how often a held lock's time is renewed: a part longer than `lock_minutes` is never taken as stale


SAY_LOG: list[Path] = []  # the running gate's `gate.log` (its waits and each command's start, read by coord-beat)


def say(text: str, console: bool = True) -> None:
    """A wait, live on the console (stderr: the status line and a quiet run's 15 lines stay on stdout), and a line
    of the run's `gate.log`: `<part>: waiting for <what>`, then `$ <part>` once that part's command starts."""
    if console:
        print(text, file=sys.stderr, flush=True)
    for log in SAY_LOG:
        try:
            with open(log, "a", encoding="utf-8") as out:
                out.write(text + "\n")
        except OSError:
            pass


def stamp() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def drop(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def hold_text(path: Path) -> str:
    """A lock's or slot's text (`<who> <time>`, a slot then `pid <n>`); "" when gone or unreadable."""
    try:
        return (path / "who" if path.is_dir() else path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def drop_if(path: Path, seen: str) -> bool:
    """Drops a lock or slot only while it is the hold judged stale (`seen`, its text): renamed away first and read
    there, so a hold another caller took since is never the one dropped; such a hold is put back. Two waiters that
    judged one stale hold: the first drops it and takes the place, the late one leaves the new hold alone."""
    away = path.with_name(f"{path.name}.drop-{os.getpid()}-{threading.get_ident()}")
    try:
        os.rename(path, away)
    except OSError:
        return False
    if hold_text(away) == seen:
        drop(away)
        return True
    try:  # put back, never over a hold taken since (a folder: rename fails onto a full one; a file: a hard link fails)
        if WINDOWS or away.is_dir():
            os.rename(away, path)
        else:
            os.link(away, path)
            away.unlink()
    except OSError:
        drop(away)  # a newer hold is in its place already
    return False


class Lock:
    """The same machine lock as `tools/work/lock.ps1` (`.work/<name>.lock`, a file) and `lock.sh` (`.work/<name>.lock.d`,
    a folder), holding `<who> <time>`. `names` are the stacks it may take, the first tried first: it holds the first
    free one, taken by trying (never by looking first). A hold older than `lock_minutes` is stale and taken over (only
    that very hold: `drop_if`); its own hold is kept fresh; it frees only its own (a timeout too). The wait has its own
    limit, `wait_minutes` (WAITED past it): a part's `timeout` starts once it holds the lock."""

    def __init__(self, config: dict, names: list[str], who: str, tag: str = ""):
        work = Path(config["main"]) / ".work"
        work.mkdir(parents=True, exist_ok=True)
        self.names = list(names)
        self.paths = [work / (f"{n}.lock" if WINDOWS else f"{n}.lock.d") for n in self.names]
        self.who, self.stale, self.wait_s = who, config["lock_minutes"] * 60, config["wait_minutes"] * 60
        self.tag, self.name, self.path, self.moved, self.mine = tag, None, None, "", ""
        self.stop, self.toucher = threading.Event(), None

    def try_take(self, path: Path) -> bool:
        while True:
            mine = f"{self.who} {stamp()}"
            try:
                if WINDOWS:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, mine.encode())
                    os.close(fd)
                else:
                    path.mkdir()
                    (path / "who").write_text(mine + "\n")
                self.mine = mine
                return True
            except FileExistsError:
                seen = hold_text(path)  # read before its age: a hold taken since is then too young to drop
                try:
                    if time.time() - path.stat().st_mtime > self.stale:
                        drop_if(path, seen)
                        continue
                except FileNotFoundError:
                    continue
                return False

    @staticmethod
    def holder(path: Path) -> str:
        """`<who> since <time>`, read from a held lock."""
        return since(hold_text(path))

    def held_by(self) -> str:
        """`e2e or e2e2, held by <who> since <time> and <who> since <time>`."""
        return f"{' or '.join(self.names)}, held by " + " and ".join(self.holder(p) for p in self.paths)

    def take(self, end: float | None = None) -> str | None:
        """None once it holds a lock; past `end` (`wait_minutes` from now: a part's slots share it), the wait's text
        (WAITED). Each new holder is said once."""
        end, seen, first = end or time.monotonic() + self.wait_s, None, ""
        while True:
            for i, path in enumerate(self.paths):
                if self.try_take(path):
                    self.name, self.path = self.names[i], path
                    if i:
                        self.moved = f"{self.names[0]} held by {first}: ran on {self.name}"
                        say(f"{self.tag}: {self.moved}")
                    self.toucher = threading.Thread(target=self.fresh, daemon=True)
                    self.toucher.start()
                    return None
                if i == 0:
                    first = self.holder(path)
            held = self.held_by()
            if held != seen:
                say(f"{self.tag}: waiting for {held}")
                seen = held
            left = end - time.monotonic()
            if left <= 0:
                return f"{self.wait_s / 60:g} min for {held}"
            time.sleep(min(WAIT_S, left))  # a lock wait inside one foreground call, not a session polling

    def fresh(self) -> None:
        while not self.stop.wait(TOUCH_S):
            try:
                os.utime(self.path)
            except OSError:
                pass

    def release(self) -> None:
        if self.path:
            self.stop.set()
            if self.toucher:
                self.toucher.join()
            if hold_text(self.path) == self.mine:  # only its own hold: one taken over since stays
                drop(self.path)
            self.path = None


def since(text: str) -> str:
    """`<who> since <time>` from a hold's text."""
    who, _, at = (text.splitlines() or [""])[0].strip().partition(" ")
    return f"{who} since {at}" if at else who or "someone"


# ---------------------------------------------------------------- CPU admission
# A part that loads this machine (any local part but a `light` one) runs only in one of `cpu_slots` (3) slots, and
# while another such run goes on, only with the CPU under `cpu_max` (90) percent. A part whose browser draws in
# software (`renderer` "software": WebGL on the CPU, as in a container) first takes one of `software_slots` (1), so it
# waits holding nothing. Why (the source project, measured): one software browser run took ~10 of 16 cores; two at once
# took 34-35 min with 37-41 load reds against 14.2 min alone, and its unit part's median grew 132/143/154 s with 0/1/2
# other gates beside it, then 261 s with 3. `cpu_max` 90 is a guess. A remote part (another machine) takes no slot; nor
# does the first run (the load is then not a gate's, and waiting would not end it). 0 slots: admission off.
# A slot is `.work/ci/<cpu|software>/slot<k>` holding `<who> <time>` then `pid <n>`, taken by creating it; a slot whose
# gate is dead (pid gone, or the pid reused: its process started after the slot was written) is taken over.

CPU_S = 3.0  # the CPU's sample
SLOT_STALE_S = 6 * 3600  # a slot older than this is dead even if its pid lives
PID = re.compile(r"^pid (\d+)$", re.M)


def cpu_percent(seconds: float = CPU_S) -> float:
    """The machine's busy CPU over `seconds`, 0-100 (Windows: `GetSystemTimes`; else the 1-min load per core)."""
    if not WINDOWS:
        return min(100.0, os.getloadavg()[0] / (os.cpu_count() or 1) * 100)
    from ctypes import wintypes

    def times():
        idle, kernel, user = (wintypes.FILETIME() for _ in range(3))
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        value = [(t.dwHighDateTime << 32) | t.dwLowDateTime for t in (idle, kernel, user)]
        return value[0], value[1] + value[2]  # kernel time includes the idle time
    idle0, all0 = times()
    time.sleep(seconds)
    idle1, all1 = times()
    return round(100.0 * (1 - (idle1 - idle0) / max(1, all1 - all0)), 1)


def alive(pid: int) -> bool:
    """Whether process `pid` runs (never signalled: `os.kill(pid, 0)` would end it on Windows)."""
    if not WINDOWS:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # access denied: it is there
    code = ctypes.c_ulong()
    try:
        k32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))
    return code.value == 259  # STILL_ACTIVE


def started(pid: int) -> float | None:
    """When process `pid` started (epoch seconds), or None when unknown (not Windows, no access, gone)."""
    if not WINDOWS:
        return None
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    handle = k32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    times = [wintypes.FILETIME() for _ in range(4)]  # creation, exit, kernel, user
    try:
        if not k32.GetProcessTimes(ctypes.c_void_p(handle), *(ctypes.byref(t) for t in times)):
            return None
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))
    return ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime) / 1e7 - 11644473600  # from 1601, 100 ns


CPU, ALIVE, STARTED = cpu_percent, alive, started  # tests give their own


def live(path: Path, text: str) -> bool:
    """A slot's gate still runs: its pid lives and started before the slot was written (else the pid is another
    process's now: pids are reused), and the slot is under `SLOT_STALE_S` old (one with no pid yet is being written,
    unless it is older than a minute)."""
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    if age > SLOT_STALE_S:
        return False
    pid = PID.search(text)
    if not pid:
        return age < 60
    if not ALIVE(int(pid.group(1))):
        return False
    born = STARTED(int(pid.group(1)))
    return born is None or born <= time.time() - age + 2  # 2 s: the clocks' grain


def loads(part: dict) -> bool:
    """Whether a part loads this machine: any local part not marked `light` (a type check, a lint)."""
    return not part.get("remote") and not part.get("light")


def software(part: dict) -> bool:
    return not part.get("remote") and part.get("renderer") == "software"


class Slot:
    """One of `limit` slots in `.work/ci/<room>/` (the CPU admission above), held while a part runs. Held back (all
    taken, or the CPU at `top` while another runs), it tries again each `WAIT_S` until `end` (WAITED)."""

    def __init__(self, config: dict, room: str, limit: int, who: str, tag: str, top: float | None = None):
        self.room, self.what, self.limit, self.top = out_dir(config) / room, room, int(limit), top
        self.who, self.tag, self.wait_m = who, tag, config["wait_minutes"]
        self.path, self.mine = None, ""

    def holders(self) -> list[str]:
        """The live slots' `<who> since <time>`, a dead gate's slot dropped on the way."""
        out = []
        for k in range(1, self.limit + 1):
            path = self.room / f"slot{k}"
            text = hold_text(path)
            if not path.exists():
                continue
            if live(path, text):
                out.append(since(text))
            else:
                drop_if(path, text)
        return out

    def try_take(self) -> bool:
        mine = f"{self.who} {stamp()}\npid {os.getpid()}"
        for k in range(1, self.limit + 1):
            path = self.room / f"slot{k}"
            try:
                with open(path, "x", encoding="utf-8") as out:
                    out.write(mine)
            except FileExistsError:
                continue
            self.path, self.mine = path, mine
            return True
        return False

    def take(self, end: float) -> str | None:
        self.room.mkdir(parents=True, exist_ok=True)
        seen = None
        while True:
            others = self.holders()
            if len(others) >= self.limit:
                where = f"{len(others)} of {self.limit} {self.what} slots"
            elif self.top is not None and others and (cpu := CPU()) >= self.top:
                where = f"the CPU under {self.top:g}% (it is {cpu:.0f}% with {len(others)} other run{'s' * (len(others) > 1)})"
            elif self.try_take():
                return None
            else:  # taken since the look: the next look says
                where = f"{len(others)} of {self.limit} {self.what} slots"
            if (where.split(" (")[0], others) != seen:
                say(f"{self.tag}: waiting for {where}" + (f", held by {' and '.join(others)}" if others else ""))
                seen = (where.split(" (")[0], others)
            left = end - time.monotonic()
            if left <= 0:
                return f"{self.wait_m:g} min for {where}"
            time.sleep(min(WAIT_S, left))  # inside one foreground call, not a session polling

    def release(self) -> None:
        if self.path and hold_text(self.path) == self.mine:
            self.path.unlink(missing_ok=True)
        self.path = None


def fill(cmd: str, got: dict) -> str:
    """`{lock}` and `{stack}` in a command: the lock the part holds and its place (from 1) in the part's `lock` list."""
    return cmd.replace("{lock}", got.get("lock", "")).replace("{stack}", str(got.get("stack", "")))


def held_env(got: dict) -> dict | None:
    """The part's environment: `CI_LOCK` and `CI_STACK` (as `fill`), and `CI_LOCK_HELD`, so a `lock.ps1`/`lock.sh` in
    its command neither waits for the lock the gate holds nor frees it."""
    if not got.get("lock"):
        return None
    return {**os.environ, "CI_LOCK": got["lock"], "CI_LOCK_HELD": got["lock"], "CI_STACK": str(got["stack"])}


def processes() -> set[str]:
    if WINDOWS:
        out = run(["tasklist", "/fo", "csv", "/nh"]).stdout
        return {line.split('","')[0].strip('"').lower() for line in out.splitlines() if line}
    return {line.strip().lower() for line in run(["ps", "-axco", "comm="]).stdout.splitlines() if line.strip()}


def busy(config: dict) -> str | None:
    """Why the machine is too busy for a `heavy` part (a perf or GPU suite), or None."""
    listed = {p.lower() for p in config["busy"].get("processes", [])}
    hit = sorted(processes() & listed) if listed else []
    return f"running: {', '.join(hit)}" if hit else None


# ---------------------------------------------------------------- one part

def export(sha: str, config: dict) -> Path:
    """The sha checked out alone (a detached worktree under `.work/ci/src/`): no lane's tree is touched."""
    tree = out_dir(config) / "src" / sha[:8]
    if tree.exists():
        remove(tree)
    git("worktree", "add", "-q", "--detach", str(tree), sha, cwd=config["main"])
    return tree


def remove(tree: Path) -> None:
    run(["git", "worktree", "remove", "--force", str(tree)], cwd=str(tree.parent))
    shutil.rmtree(tree, ignore_errors=True)
    run(["git", "worktree", "prune"], cwd=str(tree.parent))


def command(part: dict, config: dict, tree: Path, cmd: str) -> tuple[list[str] | str, bool, str]:
    """`(args, shell, cwd)` of a part's command: on the host, in the compose service (`docker`) or over ssh (`remote`)."""
    if part.get("docker"):
        d = config.get("docker") or {}
        compose = str(tree / d.get("compose", "docker-compose.ci.yml"))
        return (["docker", "compose", "-f", compose, "run", "--rm", "-T", "-v", f"{tree}:/src", "-w", "/src",
                 d.get("service", "ci"), "sh", "-c", cmd], False, str(tree))
    if part.get("remote"):
        r = config["remote"]
        repo, src = r.get("repo", "."), f".work/ci/src/{config['_sha'][:8]}"
        script = (f"cd {repo} && git worktree add -q --detach {src} {config['_sha']} && cd {src} && {{ {cmd}; }}; rc=$?; "
                  f"cd {repo} && git worktree remove --force {src}; git branch -q -D ci/{config['_sha'][:8]}; exit $rc")
        return (["ssh", r["ssh"], script], False, str(tree))
    return (cmd, True, str(tree))


def parse_junit(paths: list[Path]) -> dict:
    """Passed / failed / skipped and each red (`id` = `<classname>::<name>`, `file` when the report has one)."""
    out = {"passed": 0, "failed": 0, "skipped": 0, "reds": []}
    for path in paths:
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError) as e:
            out.setdefault("errors", []).append(f"{path.name}: {e}")
            continue
        for case in root.iter("testcase"):
            bad = case.find("failure") if case.find("failure") is not None else case.find("error")
            if bad is not None:
                text = (bad.get("message") or bad.text or "").strip().splitlines()
                out["failed"] += 1
                out["reds"].append({"id": f"{case.get('classname', '')}::{case.get('name', '')}", "name": case.get("name", ""),
                                    "classname": case.get("classname", ""), "file": case.get("file", ""),
                                    "error": (text[0] if text else "failed")[:200]})
            elif case.find("skipped") is not None:
                out["skipped"] += 1
            else:
                out["passed"] += 1
    return out


def run_part(part: dict, config: dict, tree: Path, logs: Path, sha: str) -> dict:
    config["_sha"] = sha
    name, started = part["name"], time.monotonic()
    got = {"name": name, "machine": config["remote"]["name"] if part.get("remote") else config["name"],
           "where": "remote" if part.get("remote") else "docker" if part.get("docker") else "host",
           "log": str((logs / f"{name}.log").relative_to(config["main"])).replace("\\", "/")}
    got["renderer"] = part.get("renderer", "any")
    if part.get("heavy") and (why := busy(config)):
        return {**got, "busy": True, "reason": why}
    cmd = part.get("cmd", "").strip()
    if not cmd:
        return {**got, "error": "no cmd in tools/ci/ci.json (the bootstrap batch sets it)"}
    who, slots = f"gate-{sha[:8]}", []
    lock = Lock(config, part["try_locks"], who, tag=name) if part.get("try_locks") else None
    end = time.monotonic() + config["wait_minutes"] * 60  # one wait clock: software slot, then lock, then CPU slot
    try:
        # nothing ran on a WAITED: no red, and no time off the part's own `timeout`
        if software(part) and config["software_slots"] > 0:
            slots.append(Slot(config, "software", config["software_slots"], who, name))
            if waited := slots[-1].take(end):
                return {**got, "waited": waited}
        if lock:
            if waited := lock.take(end):
                return {**got, "waited": waited}
            got.update(lock=lock.name, stack=part["locks"].index(lock.name) + 1, **({"moved": lock.moved} if lock.moved else {}))
        if loads(part) and config["cpu_slots"] > 0:
            slots.append(Slot(config, "cpu", config["cpu_slots"], who, name, top=float(config["cpu_max"])))
            if waited := slots[-1].take(end):
                return {**got, "waited": waited}
        started = time.monotonic()
        say(f"$ {name}", console=False)  # its wait is over (coord-beat's `waiting:` line drops it)
        if part.get("files"):
            cmd = part["files_cmd"].replace("{files}", " ".join(part["files"]))
        if part.get("junit"):
            for j in glob.glob(str(tree / part["junit"])):
                Path(j).unlink(missing_ok=True)  # a report from an earlier run is not this run's
        if part.get("remote"):  # the remote cannot fetch the lane's commit: push it there first
            r = config["remote"]
            pushed = run(["git", "push", "-q", "-f", r["git"], f"{sha}:refs/heads/ci/{sha[:8]}"], cwd=config["main"])
            if pushed.returncode != 0:
                return {**got, "error": f"push to the remote failed: {(pushed.stderr or '').strip()[:150]}", "rc": pushed.returncode}
        args, shell, cwd = command(part, config, tree, fill(cmd, got))
        done = run(args, cwd=cwd, shell=shell, env=held_env(got), timeout=part.get("timeout", 30) * 60,
                   log=logs / f"{name}.log")
        got["rc"] = done.returncode
        if done.returncode == -9:
            got["error"] = f"timed out after {part.get('timeout', 30)} min"
        elif part.get("junit") and not part.get("remote"):
            reports = [Path(p) for p in sorted(glob.glob(str(tree / part["junit"])))]
            got.update(parse_junit(reports))
            if not reports:
                got["error"] = f"exit {done.returncode} and no JUnit report at {part['junit']}"
            elif done.returncode != 0 and not got["reds"]:
                got["error"] = f"exit {done.returncode} with no red test"
        else:  # no per-test report: the part is one test
            ok = done.returncode == 0
            got.update({"single": True, "passed": int(ok), "failed": int(not ok), "skipped": 0,
                        "reds": [] if ok else [{"id": name, "name": name, "classname": "", "file": "",
                                                "error": tail(logs / f"{name}.log", 1)}]})
        if got.get("errors") and not got.get("error"):
            got["error"] = got["errors"][0]
    finally:
        for slot in slots:
            slot.release()
        if lock:
            lock.release()
    got["seconds"] = round(time.monotonic() - started)
    return got


def tail(path: Path, n: int) -> str:
    try:
        lines = [l for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    except OSError:
        return ""
    return " | ".join(lines[-n:])[:200]


# ---------------------------------------------------------------- known reds and the verdict

def load_known() -> list[dict]:
    path = HERE / "known-reds.json"
    return json.loads(path.read_text(encoding="utf-8")).get("reds", []) if path.exists() else []


def listed(item: dict, part: dict, known: list[dict]) -> dict | None:
    for e in known:
        if e.get("part") not in (None, "any", part["name"]):
            continue
        if not {part["machine"], "any"} & set(e.get("machines", ["any"])):
            continue
        if e.get("renderer", "any") not in ("any", part.get("renderer", "any")):
            continue
        if e.get("title"):
            if e["test"] in (item["file"], item["classname"]) and e["title"] in item["name"]:
                return e
        elif e["test"] == item["id"]:
            return e
    return None


def needed(entry: dict | None) -> int:
    """Green runs alone that make a red known: none for a listed red, 3 for a listed flake, 2 for one not listed."""
    return 2 if entry is None else 3 if entry.get("kind") == "flake" else 0


def rerun(parts: list[dict], plan_parts: list[dict], config: dict, tree: Path, logs: Path, sha: str, known: list[dict]) -> None:
    """Each red alone, as often as `needed`, then judged: listed, green alone, or new (new-flaky when green alone
    2 of 2 and not listed: the ship names it so it gets listed). A locked part's runs alone hold the lock it ran under;
    a wait for it past `wait_minutes` leaves the reds that needed them `unjudged` (the part is WAITED, never NEW)."""
    by_name = {p["name"]: p for p in plan_parts}
    for part in parts:
        part.update({"known": 0, "green_alone": 0, "new": [], "new_flaky": []})
        if part.get("error") or part.get("busy") or part.get("waited"):
            continue
        spec = by_name[part["name"]]
        lock = waited = None
        if part.get("lock") and spec.get("rerun") and any(needed(listed(r, part, known)) for r in part.get("reds", [])):
            lock = Lock(config, [part["lock"]], f"gate-{sha[:8]}", tag=part["name"])
            if waited := lock.take():
                part["waited"], part["unjudged"], lock = "rerun alone: " + waited, 0, None
        try:
            judge_reds(part, spec, config, tree, logs, known, waited)
        finally:
            if lock:
                lock.release()


def judge_reds(part: dict, spec: dict, config: dict, tree: Path, logs: Path, known: list[dict], waited) -> None:
    for i, item in enumerate(part.get("reds", [])):
        entry = listed(item, part, known)
        need = needed(entry)
        green = runs = 0
        if need and waited:
            part["unjudged"] += 1
            item.update(alone="", why="unjudged: its runs alone waited for the lock")
            continue
        if need and spec.get("rerun"):
            cmd = spec["rerun"]
            for k in ("id", "name", "classname", "file"):
                cmd = cmd.replace("{" + k + "}", item[k])
            for n in range(need):
                args, shell, cwd = command(spec, config, tree, fill(cmd, part))
                done = run(args, cwd=cwd, shell=shell, env=held_env(part), timeout=spec.get("timeout", 30) * 60,
                           log=logs / f"{part['name']}.alone{i}.{n}.log")
                runs += 1
                green += done.returncode == 0
                if done.returncode != 0:
                    break
        item["alone"] = f"{green}/{runs}" if runs else ""
        if need == 0:
            part["known"] += 1
            item["why"] = "listed"
        elif runs >= need and green == runs:
            part["green_alone"] += 1
            item["why"] = f"green alone {green} of {runs}"
            if entry is None:
                part["new_flaky"].append(item["id"])
        else:
            part["new"].append(item)


def verdict(parts: list[dict]) -> str:
    worst = 0
    for p in parts:
        worst = max(worst, 5 if p.get("error") else 4 if p.get("new") else 3 if p.get("waited") else 2 if p.get("busy")
                    else 1 if p.get("failed") else 0)
    return VERDICTS[worst]


def duration(s: float) -> str:
    m, s = divmod(int(round(s)), 60)
    return f"{m}m{s:02d}s"


def part_text(p: dict) -> str:
    if p.get("busy"):
        return f"{p['name']} BUSY ({p['reason']})"
    if p.get("error"):
        return f"{p['name']} ERROR"
    if p.get("waited") and "unjudged" not in p:  # nothing ran
        return f"{p['name']} WAITED"
    if p.get("single") and not p.get("failed"):
        return f"{p['name']} ok"
    text = f"{p['name']} {p.get('passed', 0)}/{p.get('failed', 0)}"
    if p.get("failed"):
        bits = ([f"known {p['known']}"] + ([f"alone {p['green_alone']}"] if p["green_alone"] else [])
                + ([f"waited {p['unjudged']}"] if "unjudged" in p else []) + [f"new {len(p['new'])}"])
        text += f" ({', '.join(bits)})"
    return text


def status_line(r: dict) -> str:
    bits = [f"GATE {r['sha'][:8]} {r['verdict']} {duration(r['seconds'])}"] + [part_text(p) for p in r["parts"]]
    bits += [f"{s['name']} off ({s['reason']})" for s in r["skipped"]]
    if r.get("new_flaky"):
        bits.append(f"new-flaky {len(r['new_flaky'])} ({', '.join(f['part'] + ' ' + f['id'] for f in r['new_flaky'])})")
    bits.append("shipped: " + ((r.get("shipped") or {}).get("summary") or "no"))
    return " | ".join(bits)


def details(r: dict) -> list[str]:
    lines = []
    for p in r["parts"]:
        if p.get("error"):
            lines.append(f"  ERROR {p['name']}: {p['error']} (log {p['log']})")
        if p.get("waited"):
            done = f"{p['unjudged']} red(s) unjudged" if "unjudged" in p else "nothing ran"
            lines.append(f"  WAITED {p['name']}: {p['waited']}: {done}; run again when the holder is done")
        if p.get("moved"):
            lines.append(f"  MOVED {p['name']}: {p['moved']}")
        for item in p.get("new", []):
            alone = f", alone {item['alone']}" if item.get("alone") else ""
            lines.append(f"  NEW {p['name']} {item['id']}{alone}: {item['error']} (log {p['log']})")
    return lines


def show(r: dict, quiet: bool) -> str:
    lines = [status_line(r), *details(r)]
    if quiet:
        if len(lines) > QUIET_LINES - 1:
            lines = lines[: QUIET_LINES - 2] + [f"  ... {len(lines) - QUIET_LINES + 2} more in the log"]
        lines.append(f"log: {r['logs']}")
    return "\n".join(lines)


def do_run(config: dict, plan: dict, keep: bool) -> dict:
    sha, started = plan["sha"], time.monotonic()
    name = sha[:8] + (".only" if plan["mode"] == "only" else "")
    logs = out_dir(config) / name
    if logs.exists():
        shutil.rmtree(logs, ignore_errors=True)
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "plan.txt").write_text(describe(plan) + "\n", encoding="utf-8")
    SAY_LOG[:] = [logs / "gate.log"]
    tree = export(sha, config)
    parts = []
    try:
        if config.get("setup"):
            done = run(config["setup"], cwd=str(tree), shell=True, timeout=config.get("setup_timeout", 20) * 60,
                       log=logs / "setup.log")
            if done.returncode != 0:
                parts.append({"name": "setup", "machine": config["name"], "where": "host", "rc": done.returncode,
                              "error": f"setup exit {done.returncode}: {tail(logs / 'setup.log', 1)}",
                              "log": str((logs / 'setup.log').relative_to(config["main"])).replace("\\", "/")})
        if not parts:
            parts = [run_part(p, config, tree, logs, sha) for p in plan["parts"]]
            rerun(parts, plan["parts"], config, tree, logs, sha, load_known())
    finally:
        if not keep:
            remove(tree)
    flaky = [{"part": p["name"], "id": i} for p in parts for i in p.get("new_flaky", [])]
    result = {"sha": sha, "verdict": verdict(parts), "seconds": round(time.monotonic() - started), "mode": plan["mode"],
              "base": plan["base"], "options": plan["options"], "parts": parts, "skipped": plan["skipped"],
              "new_flaky": flaky, "shipped": {}, "logs": str(logs.relative_to(config["main"])).replace("\\", "/") + "/"}
    result["line"] = status_line(result)
    data = json.dumps(result, indent=1)
    (out_dir(config) / f"{name}.json").write_text(data, encoding="utf-8")
    (logs / "result.json").write_text(data, encoding="utf-8")
    return result


# ---------------------------------------------------------------- ship

class ShipError(Exception):
    def __init__(self, step: str, reason: str):
        super().__init__(step)
        self.step, self.reason = step, " ".join(reason.split())[:200]


def main_worktree(config: dict) -> Path:
    """The worktree with `main` checked out (`ship.tree` in the config overrides)."""
    if (config.get("ship") or {}).get("tree"):
        return Path(config["ship"]["tree"])
    tree = None
    for line in git("worktree", "list", "--porcelain", cwd=config["main"]).splitlines():
        if line.startswith("worktree "):
            tree = line[9:]
        elif line.strip() == "branch refs/heads/main" and tree:
            return Path(tree)
    raise SystemExit("no worktree has main checked out")


def ship(sha: str, a, config: dict) -> str:
    """Only after a GREEN or KNOWN-RED gate of this very sha, from the main worktree at that sha. In order: fetch
    (MOVED when origin/main is no longer under the sha), the ROADMAP check, the merge log and its commit, the push,
    the vault, the deploy (not for a docs-only diff) and its health check, the remote's fast-forward."""
    short, tree, steps = sha[:8], main_worktree(config), []
    path = out_dir(config) / f"{short}.json"
    result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if git("rev-parse", "HEAD", cwd=tree) != sha and not a.dry_run:
        return f"SHIP {short} FAIL tree: {tree} is not at the gated sha"

    def step(name: str, args, check=True, shell=False, env=None) -> str:
        steps.append(name)
        if a.dry_run:
            print(f"  would run ({name}): {args if isinstance(args, str) else ' '.join(map(str, args))}")
            return ""
        done = run(args, cwd=str(tree), shell=shell, env=env, timeout=30 * 60)
        if check and done.returncode != 0:
            raise ShipError(name, (done.stderr or done.stdout or f"exit {done.returncode}").strip().splitlines()[-1])
        return done.stdout

    try:
        if run(["git", "fetch", "-q", "origin"], cwd=str(tree)).returncode != 0:
            raise ShipError("fetch", "git fetch failed")
        if not result or result.get("sha") != sha or result.get("mode") == "only":
            return f"SHIP {short} NOT-GATED: no full gate of this sha (run `gate.py run {short}` first)"
        if result["verdict"] not in SHIPPABLE:
            return f"SHIP {short} NOT-GREEN: the gate said {result['verdict']}"
        if run(["git", "merge-base", "--is-ancestor", "origin/main", sha], cwd=str(tree)).returncode != 0:
            return f"SHIP {short} MOVED: origin/main moved past the merge's base; merge it and gate again"
        files = git("diff", "--name-only", "origin/main", sha, cwd=tree).split()
        roadmap = run(["git", "show", f"{sha}:docs/ROADMAP.md"], cwd=str(tree)).stdout
        open_rows = [l for l in roadmap.splitlines() if l.startswith("|") and f"`{a.batch}`" in l and "✅" not in l]
        if open_rows and not a.no_roadmap:
            return f"SHIP {short} ROADMAP: `{a.batch}` has a docs/ROADMAP.md row not marked ✅ (or --no-roadmap)"
        code = [f for f in files if not matches(f, config["docs_only"])]
        cfg = config.get("ship") or {}
        up = a.up or ((f"yes, redeployed and healthy" if cfg.get("deploy") else "no deploy configured") if code else "no, docs only")
        if (tree / "tools/work/merge-log.py").exists() and (tree / "docs/MERGES.md").exists():
            cause = a.from_ or cause_of(a.batch, tree, config["main"])  # the spec, then the report's From: line
            if not cause:
                raise ShipError("cause", f"NO CAUSE: no --from, no docs/specs/{a.batch}.md, no From: line in its report")
            step("merge-log", [sys.executable, "-I", "tools/work/merge-log.py", "--batch", a.batch, "--sha", short,
                               "--what", a.what, "--up", up, "--try", a.try_, "--from", cause])
            step("commit", ["git", "commit", "-q", "-m", f"Merge log: {a.batch}", "--", "docs/MERGES.md"])
        pushed = sha if a.dry_run else git("rev-parse", "HEAD", cwd=tree)
        step("push", ["git", "push", "-q", "origin", "HEAD:main"])
        vault = "skip"
        if (tree / "tools/vault/vaultify.py").exists():
            out = step("vault", [sys.executable, "-I", "tools/vault/vaultify.py", "--rev", "origin/main"], check=False)
            try:
                v = json.loads(out.strip().splitlines()[-1])
                vault = f"{v['written']}/{v['misses']}"
            except (IndexError, ValueError, KeyError):
                vault = "dry" if a.dry_run else "?"
        deployed = "no"
        if code and cfg.get("deploy") and config.get("autodeploy") and not a.dry_run:
            import autodeploy                                # the app follows main: its runner and lock, no 2nd build
            steps.append("deploy")
            autodeploy.mark(config)
            if autodeploy.run(config, tree, wait=True):
                raise ShipError("deploy", (autodeploy.read_state(config).get("tail") or ["autodeploy: FAIL"])[-1])
            deployed = "yes"
        elif code and cfg.get("deploy"):
            step("deploy", cfg["deploy"], shell=True)
            deployed = "yes"
            if cfg.get("health") and not a.dry_run:
                healthy(cfg["health"])
        remote = remote_ff(config, step, short)
    except ShipError as e:
        line = f"SHIP {short} FAIL {e.step}: {e.reason}"
    else:
        flaky = result.get("new_flaky") or []
        line = (f"SHIP {short} OK pushed={pushed[:8]} deploy={deployed} vault={vault} remote={remote}"
                + (f" new-flaky={','.join(f['part'] + ':' + f['id'] for f in flaky)} (list it in tools/ci/known-reds.json)" if flaky else "")
                + (" (dry run: nothing changed)" if a.dry_run else ""))
        if not a.dry_run:
            result["shipped"] = {"summary": f"{pushed[:8]} deploy={deployed}", "line": line, "steps": steps}
            result["line"] = status_line(result)
            path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return line


def cause_of(batch: str, tree: Path, main) -> str:
    """The merge's default cause (tools/work/cause.py, `merge-cause`): the spec in `tree`, then the `From:` line of
    `<main>/.work/<id>/report.md`; "" when there is none or the tree has no cause.py."""
    sys.path.insert(0, str(Path(tree) / "tools" / "work"))
    try:
        from cause import cause_in
    except ImportError:
        return ""
    return cause_in(batch, Path(tree), Path(main))


def healthy(url: str) -> None:
    for _ in range(10):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return
        except OSError:
            time.sleep(3)
    raise ShipError("health", f"{url} did not answer 200")


def remote_ff(config: dict, step, short: str) -> str:
    """The remote machine's checkout fast-forwarded to the pushed main (a push of a temp branch, then `--ff-only`
    there). `skip` with no `remote.git`; a failure is reported, the ship stands."""
    r = config.get("remote") or {}
    if not r.get("git") or config.get("no_remote"):
        return "skip"
    tmp = f"ci/ship-{short}"
    try:
        step("remote-push", ["git", "push", "-q", "-f", r["git"], f"HEAD:refs/heads/{tmp}"])
        step("remote-ff", ["ssh", r["ssh"], f"cd {r.get('repo', '.')} && {{ git merge -q --ff-only {tmp}; rc=$?; git branch -q -D {tmp}; exit $rc; }}"])
    except ShipError as e:
        return f"fail({e.reason[:60]})"
    return "ok"


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="this machine's config (default: <main tree>/.work/ci/machine.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("sha", nargs="?", default="HEAD")
        p.add_argument("--base", help="what the diff is measured against (default: ci.json's base, origin/main)")
        group = p.add_mutually_exclusive_group()
        group.add_argument("--parts", nargs="+", help="these parts only, whatever the diff")
        group.add_argument("--all", action="store_true", help="every part")
        group.add_argument("--only", nargs="+", help="quiet: part names or test files (a part's `match`); never shippable")
        p.add_argument("--no-remote", action="store_true", help="this machine only: no part on the remote machine")
        p.add_argument("--stack", type=int, help="each locked part on the Nth lock of its `lock` list, never moved "
                                                  "(without it a --only run takes a free one by itself)")
        if name == "run":
            p.add_argument("--keep", action="store_true", help="keep the export (.work/ci/src/<sha8>)")
        else:
            p.add_argument("--json", action="store_true")
    p = sub.add_parser("status")
    p.add_argument("sha", nargs="?", default="HEAD")
    p = sub.add_parser("ship")
    p.add_argument("sha")
    p.add_argument("--batch", required=True)
    p.add_argument("--what", required=True)
    p.add_argument("--try", dest="try_", required=True)
    p.add_argument("--up", help="the merge log's Up (default: from the diff and the deploy)")
    p.add_argument("--from", dest="from_", help="the merge log's From (default: the spec, the report's From: line)")
    p.add_argument("--no-roadmap", action="store_true", help="ship though the batch's ROADMAP row is not marked done")
    p.add_argument("--dry-run", action="store_true", help="print the steps, change nothing")
    a = ap.parse_args(argv)
    config = load(a.config)
    sha = git("rev-parse", f"{a.sha}^{{commit}}")
    if a.cmd == "ship":
        line = ship(sha, a, config)
        print(line)
        return 0 if " OK " in f"{line} " else 1
    if a.cmd == "status":
        path = out_dir(config) / f"{sha[:8]}.json"
        if not path.exists():
            print(f"GATE {sha[:8]} NONE: no run yet ({path})")
            return 1
        print(show(json.loads(path.read_text(encoding="utf-8")), quiet=False))
        return 0
    if a.no_remote:
        config = {**config, "no_remote": True}
    picked = [p["name"] for p in config["parts"]] if a.all else a.parts
    if a.stack is not None and a.stack < 1:
        raise SystemExit("--stack: 1 or more")
    plan = build_plan(config, sha, a.base or config["base"], picked, getattr(a, "only", None), a.stack)
    if a.cmd == "plan":
        print(json.dumps(plan, indent=1) if a.json else describe(plan))
        return 0
    result = do_run(config, plan, a.keep)
    print(show(result, quiet=plan["mode"] == "only"))
    return 0 if result["verdict"] in SHIPPABLE else 1


if __name__ == "__main__":
    sys.exit(main())
