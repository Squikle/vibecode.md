"""The machine's background jobs from the checked-in manifest `tools/jobs/jobs.json`, as opencode automations.
Python only, so Windows and macOS run the same commands.

python -I -X utf8 tools/jobs/jobs.py list               each job and the command its automation runs
python -I -X utf8 tools/jobs/jobs.py sync [--apply]     what to create, replace or delete so the live `<prefix>*`
    automations (jobs.json `prefix`) match the manifest; without --apply it only prints the calls (the shell `opencode api` line and the
    in-session `tools.automation.create(...)` form). With --apply it makes them through the server's
    `experimental.automation.create` / `.delete` (`opencode api`, the running background service).
python -I -X utf8 tools/jobs/jobs.py run <job>          runs one job now (what the automation calls); an exit code
    in the job's `alert_exit` (jobs.json; role-telemetry's 1: a NEW flag) prints an ALERT line and exits 0, so the
    automation reads as fired, not failed; any other non-zero exit is passed on.

Run `sync` from the checkout the jobs should run from (the main tree, or the worktree that holds `main`):
its path goes into each command. The outputs go to the main tree's `.work` (`{work}`).
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MANIFEST = HERE / "jobs.json"
sys.path.insert(0, str(ROOT / "tools" / "work"))  # `python -I` leaves it off
from role_data import main_work  # noqa: E402  (the one reading of "the main tree's .work")


def load(path=MANIFEST):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def jobs_here(man, platform=sys.platform):
    return [j for j in man["jobs"] if not j.get("os") or platform in j["os"]]


def quoted(path, platform=sys.platform):
    p = str(path).replace("\\", "/")
    if " " not in p:
        return p
    return f'& "{p}"' if platform == "win32" else f'"{p}"'


def quoted_arg(path):
    p = str(path).replace("\\", "/")
    return f'"{p}"' if " " in p else p


def command(job, root=ROOT, python=None, platform=sys.platform):
    py = quoted(python or sys.executable, platform)
    return f"{py} -I -X utf8 {quoted_arg(root / 'tools' / 'jobs' / 'jobs.py')} run {job['name']}"


def desired(man, root=ROOT, python=None, platform=sys.platform):
    return {man["prefix"] + j["name"]: {"name": man["prefix"] + j["name"], "trigger": {"type": "every", "every": j["every"]},
                                        "action": {"type": "shell", "command": command(j, root, python, platform)},
                                        "directory": str(root).replace("\\", "/")}
            for j in jobs_here(man, platform)}


def plan(want, live, prefix):
    """(create, delete): bodies to create, ids to delete; a changed job is a delete plus a create."""
    create, delete = [], []
    ours = {a["name"]: a for a in live if a.get("name", "").startswith(prefix) and a.get("status") != "done"}
    for name, body in want.items():
        have = ours.get(name)
        same = have and have.get("trigger", {}).get("every") == body["trigger"]["every"] and \
            have.get("action", {}).get("command") == body["action"]["command"]
        if have and not same:
            delete.append(have["id"])
        if not same:
            create.append(body)
    delete += [a["id"] for n, a in ours.items() if n not in want]
    return create, delete


def opencode():
    exe = shutil.which("opencode")
    if exe and exe.lower().endswith(".cmd"):  # npm's shim: call the binary, so a JSON body keeps its quotes
        real = pathlib.Path(exe).parent / "node_modules" / "@opencode" / "cli" / "bin" / "opencode.exe"
        exe = str(real) if real.is_file() else exe
    return exe


def api(*args):
    exe = opencode()
    if not exe:
        raise SystemExit("jobs: no `opencode` on PATH; create the automations in a session (the printed calls)")
    out = subprocess.run([exe, "api", *args], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if out.returncode:
        raise SystemExit(f"jobs: opencode api {args[0]}: {(out.stderr or out.stdout).strip()[:300]}")
    return out.stdout


def live_automations():
    text = api("experimental.automation.list")
    data = json.JSONDecoder().raw_decode(text[text.find("{"):])[0]
    return data.get("data", [])


def sync(man, apply, root=ROOT):
    want = desired(man, root)
    try:
        live = live_automations()
    except SystemExit as e:
        print(f"{e} -> treating every job as missing")
        live = []
    create, delete = plan(want, live, man["prefix"])
    for i in delete:
        print(f"delete: opencode api experimental.automation.delete --param automationID={i}")
        print(f"   in a session: tools.automation.delete({{ id: {json.dumps(i)} }})")
        if apply:
            api("experimental.automation.delete", "--param", f"automationID={i}")
    for body in create:
        print(f"create: opencode api experimental.automation.create -d '{json.dumps(body)}'")
        tool = {k: v for k, v in body.items() if k != "directory"}
        print(f"   in a session: tools.automation.create({json.dumps(tool)})")
        if apply:
            api("experimental.automation.create", "-d", json.dumps(body))
    if not create and not delete:
        print("jobs: the live automations match the manifest")
    elif not apply:
        print("jobs: dry run; --apply makes these calls")
    return 0


def run(man, name, root=ROOT):
    job = next((j for j in man["jobs"] if j["name"] == name), None)
    if not job:
        raise SystemExit(f"jobs: no job {name!r} in {MANIFEST}")
    work = str(main_work(root)).replace("\\", "/")
    args = [a.replace("{work}", work).replace("{root}", str(root).replace("\\", "/")) for a in job["run"]]
    script = root / args[0]
    done = subprocess.run([sys.executable, "-I", "-X", "utf8", str(script), *args[1:]], cwd=root, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1"})
    alert = job.get("alert_exit", [])
    if done.returncode in alert:
        print(f"ALERT {name}: exit {done.returncode} (see its log)")
    tail = (done.stdout.strip().splitlines() or [""])[:3] + (done.stderr.strip().splitlines()[-5:] if done.stderr else [])
    print("\n".join(tail))
    return 0 if done.returncode in [0] + alert else done.returncode


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("list", "sync", "run"):
        print(__doc__)
        return 2
    man = load()
    if argv[0] == "list":
        for j in jobs_here(man):
            print(f"{man['prefix']}{j['name']}  every {j['every']}  {command(j)}")
        return 0
    if argv[0] == "sync":
        return sync(man, "--apply" in argv)
    if len(argv) < 2:
        raise SystemExit("jobs: run <job>")
    return run(man, argv[1])


if __name__ == "__main__":
    sys.exit(main())
