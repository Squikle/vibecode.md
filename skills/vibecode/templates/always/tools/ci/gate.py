#!/usr/bin/env python3
"""The local CI gate (`tools/ci/README.md`): one call per sha, one status line. Stack-agnostic: `tools/ci/ci.json`
names the parts (test kinds), their commands, the paths that trigger each and where its JUnit report lands.

    python tools/ci/gate.py plan [sha] [--base origin/main] [--parts a b] [--no-remote]   what would run, and why
    python tools/ci/gate.py run [sha] [same options] [--keep]       the gate: `.work/ci/<sha8>.json` + one line
    python tools/ci/gate.py run [sha] --only unit tests/test_x.py   quiet (<= 15 lines), never shippable
    python tools/ci/gate.py status [sha]                            the last run's line again
    python tools/ci/gate.py ship <sha> --batch <id> --what .. --try .. [--up ..] [--no-roadmap] [--dry-run]

Verdicts: GREEN, KNOWN-RED (every red listed in known-reds.json, or green alone 2 of 2; 3 of 3 for a listed flake),
NEW-RED, BUSY (a heavy part skipped: the machine is busy), ERROR (the gate or a runner broke). Exit 0 only for the
first two. Stdlib only; Windows, macOS and Linux. Unknown arguments are an error (argparse), never guessed."""
import argparse
import datetime
import fnmatch
import glob
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if WINDOWS else 0
QUIET_LINES = 15
SHIPPABLE = ("GREEN", "KNOWN-RED")
VERDICTS = ("GREEN", "KNOWN-RED", "BUSY", "NEW-RED", "ERROR")
DEFAULTS = {"base": "origin/main", "lock_minutes": 50, "parts": [], "busy": {"processes": []},
            "docs_only": ["docs/*", "*.md"], "ship": {}}
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


def build_plan(config: dict, sha: str, base: str, picked: list[str] | None, only: list[str] | None) -> dict:
    parts, skipped, why = [], [], {}
    known = {p["name"]: p for p in config["parts"]}
    if only:
        files: dict[str, list[str]] = {}
        for item in only:
            if item in known:
                files.setdefault(item, [])
                continue
            item = item.replace("\\", "/")
            owner = next((p for p in config["parts"] if p.get("files_cmd") and matches(item, p.get("match", []))), None)
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
    return {"sha": sha, "base": base, "mode": mode, "files": len(changed), "parts": parts, "why": why, "skipped": skipped,
            "options": {"no_remote": bool(config.get("no_remote"))}}


def describe(plan: dict) -> str:
    lines = [f"PLAN {plan['sha'][:8]} ({plan['mode']}, {plan['files']} files vs {plan['base'] or '-'})"]
    for p in plan["parts"]:
        where = "remote" if p.get("remote") else "docker" if p.get("docker") else "host"
        extra = f" files={' '.join(p['files'])}" if p.get("files") else ""
        lines.append(f"  run  {p['name']} [{where}]{extra}: {plan['why'].get(p['name'], '')}")
    for s in plan["skipped"]:
        lines.append(f"  skip {s['name']}: {s['reason']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- locks and the busy guard

class Lock:
    """The same machine lock as `tools/work/lock.ps1` (`.work/<name>.lock`, a file) and `lock.sh` (`.work/<name>.lock.d`,
    a folder). Waits for its holder; a hold older than `lock_minutes` is stale and taken over; frees only its own."""

    def __init__(self, config: dict, name: str, who: str):
        work = Path(config["main"]) / ".work"
        work.mkdir(parents=True, exist_ok=True)
        self.path = work / (f"{name}.lock" if WINDOWS else f"{name}.lock.d")
        self.who, self.stale, self.mine = who, config["lock_minutes"] * 60, False

    def take(self, wait_s: float) -> bool:
        end = time.monotonic() + wait_s
        while True:
            try:
                if WINDOWS:
                    fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, f"{self.who} {datetime.datetime.now().isoformat()}".encode())
                    os.close(fd)
                else:
                    self.path.mkdir()
                    (self.path / "who").write_text(f"{self.who} {datetime.datetime.now().isoformat()}\n")
                self.mine = True
                return True
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale:
                        self.drop()
                        continue
                except FileNotFoundError:
                    continue
            if time.monotonic() > end:
                return False
            time.sleep(10)  # a lock wait inside one foreground call, not a session polling

    def drop(self) -> None:
        if self.path.is_dir():
            shutil.rmtree(self.path, ignore_errors=True)
        else:
            self.path.unlink(missing_ok=True)

    def release(self) -> None:
        if self.mine:
            self.drop()
            self.mine = False


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
    if part.get("heavy") and (why := busy(config)):
        return {**got, "busy": True, "reason": why}
    lock = Lock(config, part["lock"], f"gate {sha[:8]}") if part.get("lock") else None
    if lock and not lock.take(config["lock_minutes"] * 60):
        return {**got, "busy": True, "reason": f"the {part['lock']} lock stayed held {config['lock_minutes']} min"}
    try:
        cmd = part.get("cmd", "").strip()
        if not cmd:
            return {**got, "error": "no cmd in tools/ci/ci.json (the bootstrap batch sets it)"}
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
        args, shell, cwd = command(part, config, tree, cmd)
        done = run(args, cwd=cwd, shell=shell, timeout=part.get("timeout", 30) * 60, log=logs / f"{name}.log")
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
    2 of 2 and not listed: the ship names it so it gets listed)."""
    by_name = {p["name"]: p for p in plan_parts}
    for part in parts:
        part.update({"known": 0, "green_alone": 0, "new": [], "new_flaky": []})
        if part.get("error") or part.get("busy"):
            continue
        spec = by_name[part["name"]]
        for i, item in enumerate(part.get("reds", [])):
            entry = listed(item, part, known)
            need = needed(entry)
            green = runs = 0
            if need and spec.get("rerun"):
                cmd = spec["rerun"]
                for k in ("id", "name", "classname", "file"):
                    cmd = cmd.replace("{" + k + "}", item[k])
                for n in range(need):
                    args, shell, cwd = command(spec, config, tree, cmd)
                    done = run(args, cwd=cwd, shell=shell, timeout=spec.get("timeout", 30) * 60,
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
        worst = max(worst, 4 if p.get("error") else 3 if p.get("new") else 2 if p.get("busy") else 1 if p.get("failed") else 0)
    return VERDICTS[worst]


def duration(s: float) -> str:
    m, s = divmod(int(round(s)), 60)
    return f"{m}m{s:02d}s"


def part_text(p: dict) -> str:
    if p.get("busy"):
        return f"{p['name']} BUSY ({p['reason']})"
    if p.get("error"):
        return f"{p['name']} ERROR"
    if p.get("single") and not p.get("failed"):
        return f"{p['name']} ok"
    text = f"{p['name']} {p.get('passed', 0)}/{p.get('failed', 0)}"
    if p.get("failed"):
        bits = [f"known {p['known']}"] + ([f"alone {p['green_alone']}"] if p["green_alone"] else []) + [f"new {len(p['new'])}"]
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
            step("merge-log", [sys.executable, "-I", "tools/work/merge-log.py", "--batch", a.batch, "--sha", short,
                               "--what", a.what, "--up", up, "--try", a.try_])
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
    plan = build_plan(config, sha, a.base or config["base"], picked, getattr(a, "only", None))
    if a.cmd == "plan":
        print(json.dumps(plan, indent=1) if a.json else describe(plan))
        return 0
    result = do_run(config, plan, a.keep)
    print(show(result, quiet=plan["mode"] == "only"))
    return 0 if result["verdict"] in SHIPPABLE else 1


if __name__ == "__main__":
    sys.exit(main())
