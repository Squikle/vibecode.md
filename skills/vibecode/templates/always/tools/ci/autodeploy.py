#!/usr/bin/env python3
"""The owner's app follows `main`: when main moves, the worktree that has `main` redeploys by itself, in the background,
one deploy at a time, so a merge with no conflict is built and running with no one asking. Stdlib only (Windows, macOS, Linux).

python -I tools/ci/autodeploy.py kick     what the vault hook's `run` calls on main: marks the move, starts a detached
                                          `run-marked`, returns at once. Opt-in per machine: `"autodeploy": true` in
                                          `<main tree>/.work/ci/machine.json`; without it, nothing
python -I tools/ci/autodeploy.py run      marks and deploys now, in the foreground (waits for a running one: the ship)
python -I tools/ci/autodeploy.py status   one line: the last deploy (`ok`, `skip`, `FAIL` and its last line), a running one;
                                          exit 1 on FAIL

A deploy is the ship's: `ship.deploy` from `tools/ci/ci.json` (the machine's `.work/ci/machine.json` over it), run in this
worktree, then `ship.health` must answer 200. Skipped: HEAD already deployed, or every file changed since the last good
deploy matches `autodeploy_idle` (default: `docs_only` plus the tests and the repo's own tools). While it runs, a new move
only marks `pending`: the running one deploys again. Files: `<main tree>/.work/deploy/` state.json, deploy.log, run.lock."""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import gate  # noqa: E402  (load, run, git, matches; importing it runs nothing)

IDLE = ["tests/*", "test/*", "tools/ci/*", "tools/work/*", "tools/vault/*"]
STALE_S = 30 * 60          # a lock older than this is a dead run's (a deploy times out at 20 min)
TIMEOUT_S = 20 * 60
LOG_KB = 256
WORDS = ("kick", "run", "run-marked", "status")


def files(config: dict) -> dict:
    d = Path(config["main"]) / ".work" / "deploy"
    return {"dir": d, "state": d / "state.json", "log": d / "deploy.log", "lock": d / "run.lock", "pending": d / "pending"}


def read_state(config: dict) -> dict:
    try:
        return json.loads(files(config)["state"].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def idle_globs(config: dict) -> list[str]:
    return config.get("autodeploy_idle") or [*config.get("docs_only", []), *IDLE]


def idle(changed: list[str], config: dict) -> bool:
    """True when no changed file reaches the running app: docs, tests and the repo's own tools only."""
    globs = idle_globs(config)
    return bool(changed) and all(gate.matches(p, globs) for p in changed)


def mark(config: dict) -> None:
    f = files(config)
    f["dir"].mkdir(parents=True, exist_ok=True)
    f["pending"].write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")


def spawn(args: list[str]) -> None:
    """`args` detached: no console window, out of git's process group (and its job, when Windows allows it)."""
    kw = dict(cwd=str(HERE.parents[1]), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              close_fds=True)
    if not gate.WINDOWS:
        subprocess.Popen(args, start_new_session=True, **kw)
        return
    flags = gate.NO_WINDOW | 0x00000200                               # CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(args, creationflags=flags | 0x01000000, **kw)  # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(args, creationflags=flags, **kw)


def off(config: dict) -> str | None:
    """Why it does nothing on this machine, or None."""
    if not config.get("autodeploy"):
        return "deploy: off on this machine"
    if not (config.get("ship") or {}).get("deploy"):
        return "deploy: off (no ship.deploy in tools/ci/ci.json)"
    return None


def kick(config: dict | None = None, spawn=spawn) -> str:
    config = config or gate.load(None)
    why = off(config)
    if why:
        return why
    mark(config)
    spawn([sys.executable, "-I", str(Path(__file__).resolve()), "run-marked"])
    return "deploy: kicked"


def take(lock: Path, wait: bool) -> bool:
    end = time.time() + STALE_S
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {time.time():.0f}".encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > STALE_S:
                    lock.unlink()
                    continue
            except OSError:
                continue
            if not wait or time.time() > end:
                return False
            time.sleep(5)


def deploy_cmd(config: dict, tree: Path) -> tuple[int, list[str]]:
    done = gate.run(config["ship"]["deploy"], cwd=str(tree), shell=True, timeout=TIMEOUT_S)
    if done.returncode == -9:
        return 124, [f"ship.deploy: no end in {TIMEOUT_S // 60} min"]
    return done.returncode, (done.stdout + done.stderr).strip().splitlines()[-12:]


def healthy(config: dict) -> bool:
    url = (config.get("ship") or {}).get("health")
    if not url:
        return True
    for _ in range(10):
        try:
            with urllib.request.urlopen(url, timeout=5) as answer:
                if answer.status == 200:
                    return True
        except OSError:
            time.sleep(3)
    return False


def log(config: dict, lines: list[str]) -> None:
    p = files(config)["log"]
    if p.is_file() and p.stat().st_size > LOG_KB * 1024:
        keep = p.read_bytes()[-LOG_KB * 512:]
        p.write_bytes(keep[keep.find(b"\n") + 1:])
    with p.open("a", encoding="utf-8") as f:
        f.write("".join(l.rstrip("\n") + "\n" for l in lines))


def once(config: dict, tree: Path, deploy, check) -> None:
    """Deploys HEAD unless it runs already or nothing deployable changed; records the word in state.json and the log."""
    state, t0 = read_state(config), time.time()
    head = gate.git("rev-parse", "HEAD", cwd=tree)
    good, tail = state.get("deployed"), []
    if gate.run(["git", "symbolic-ref", "-q", "--short", "HEAD"], cwd=str(tree)).stdout.strip() != "main":
        word, why = "skip", "not on main"
    elif good == head:
        word, why = "skip", "it runs already"
    else:
        diff = gate.run(["git", "diff", "--name-only", good, head], cwd=str(tree)) if good else None
        changed = diff.stdout.split() if diff and diff.returncode == 0 else None
        if changed is not None and idle(changed, config):
            word, why, good = "skip", f"docs, tests and tools only ({len(changed)} files)", head
        else:
            rc, tail = deploy(config, tree)
            if rc == 0 and not check(config):
                rc, tail = 1, tail + [f"{config['ship']['health']} did not answer 200"]
            word, why = ("ok", "") if rc == 0 else ("FAIL", f"exit {rc}")
            good = head if rc == 0 else good
    at = time.strftime("%Y-%m-%d %H:%M:%S")
    took = round(time.time() - t0)
    files(config)["state"].write_text(json.dumps({"word": word, "head": head, "deployed": good, "at": at, "took": took,
                                                  "why": why, "tail": tail if word == "FAIL" else []}, indent=1),
                                      encoding="utf-8")
    log(config, [f"{at} {head[:8]} {word} {took}s {why}".rstrip()] + (["  " + l for l in tail] if word == "FAIL" else []))


def run(config: dict, tree: Path, deploy=deploy_cmd, check=healthy, wait: bool = False) -> int:
    """Deploys while `pending` is marked, one run at a time; a run that finds the lock leaves the mark to its holder."""
    f = files(config)
    f["dir"].mkdir(parents=True, exist_ok=True)
    while f["pending"].exists():
        if not take(f["lock"], wait):
            return 0
        try:
            while f["pending"].exists():
                f["pending"].unlink(missing_ok=True)
                once(config, tree, deploy, check)
        finally:
            f["lock"].unlink(missing_ok=True)
    return 1 if read_state(config).get("word") == "FAIL" else 0


def status_line(config: dict) -> str:
    s, f = read_state(config), files(config)
    busy = f["lock"].exists() and time.time() - f["lock"].stat().st_mtime < STALE_S
    line = "deploy: none yet" if not s else f"deploy {s['word']} {s['head'][:8]} {s['at'][11:16]} {s['took']}s {s['why']}".rstrip()
    if s.get("word") == "FAIL" and s.get("tail"):
        line += ": " + s["tail"][-1][:160]
    return line + (" | running" if busy else "") + (" | pending" if f["pending"].exists() else "")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in WORDS:              # live state: an unknown argument is refused, never guessed
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    config, tree = gate.load(None), HERE.parents[1]
    if argv[0] == "kick":
        print(kick(config))
        return 0
    if argv[0] == "status":
        print(status_line(config))
        return 1 if read_state(config).get("word") == "FAIL" else 0
    why = off(config)
    if why:
        print(why, file=sys.stderr)
        return 0
    if argv[0] == "run":
        mark(config)
    return run(config, tree, wait=argv[0] == "run")


if __name__ == "__main__":
    sys.exit(main())
