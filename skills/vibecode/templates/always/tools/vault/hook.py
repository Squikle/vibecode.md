#!/usr/bin/env python3
"""The vault rebuilds when `main` moves: git hooks that run vaultify. Stdlib only (Windows and macOS).

python -I tools/vault/hook.py install [--vault <dir>] [--python <exe>]   (once per clone; every worktree shares the hooks)
python -I tools/vault/hook.py remove | print

`install` writes `post-merge` and `post-commit` (POSIX sh, marked `vaultify-git-hook`) into `git rev-parse --git-path hooks`,
so `core.hooksPath` is honoured. post-merge fires on a merge, a `pull`, a `merge --ff-only`; post-commit on a merge finished
by `git commit` after a conflict and on the merge-log commit. Each acts only when the worktree's branch is `main`, and runs
the tree's own `tools/vault/hook.py run`: `vaultify --rev HEAD --if-changed` (the commit that just landed), one line per run
in `<vault>/../vault-hook.log` (a miss's details indented under it), one line on git's stderr only on a miss or an error, exit 0 always.
A hook that is not ours is moved to `<name>.vault-chained` and run first, with git's arguments; its exit code is kept.
When that slot is taken the install refuses and changes nothing. `remove` deletes ours and puts the chained one back.
Default vault: `<main tree>/.work/vault` (the parent of the git common dir). `print` shows the plan and writes nothing.
"""
import argparse, io, json, os, subprocess, sys, time, traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARK = "vaultify-git-hook"
NAMES = ("post-merge", "post-commit")
CHAIN = ".vault-chained"
LOG_KB = 256                       # the log keeps its newer half past this

SH = """#!/bin/sh
# {mark}: rebuilds the vault when main moves (tools/vault/hook.py; `hook.py remove` takes it out)
rc=0
c="${{0%/*}}/{name}{chain}"
if [ -x "$c" ]; then "$c" "$@"; rc=$?; fi
# The branch from the HEAD file with shell builtins: a commit on a lane, in any worktree, starts no process.
d="${{GIT_DIR:-.git}}"
if [ -f "$d" ]; then read -r d < "$d"; d="${{d#gitdir: }}"; fi
h=
if [ -f "$d/HEAD" ]; then read -r h < "$d/HEAD"; fi
case "$h" in
  "ref: refs/heads/main") ;;
  ""|"ref: refs/heads/.invalid") [ "$(git symbolic-ref -q HEAD)" = refs/heads/main ] || exit $rc ;;   # unread, reftable
  *) exit $rc ;;                                                     # another branch, a detached HEAD (a rebase)
esac
t="$(git rev-parse --show-toplevel)/tools/vault/hook.py"
if [ -f "$t" ]; then '{py}' -I "$t" run {name} --vault '{vault}' </dev/null || :; fi
exit $rc
"""


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise SystemExit(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def where(repo, vault):
    """(the hooks dir, the vault) of the repo: hooks from git (core.hooksPath aside), the vault by default in the main tree."""
    top = git(repo, "rev-parse", "--show-toplevel")
    hooks = Path(top) / git(top, "rev-parse", "--path-format=absolute", "--git-path", "hooks")
    common = Path(git(top, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    return hooks.resolve(), Path(vault).resolve() if vault else (common.parent / ".work" / "vault").resolve()


def ours(p):
    return p.is_file() and MARK.encode() in p.read_bytes()[:400]


def plan(hooks):
    """Per hook: (what is there, what install does): install, rewrite, chain, or refuse."""
    out = {}
    for n in NAMES:
        p, c = hooks / n, hooks / (n + CHAIN)
        if not p.exists():
            out[n] = ("missing", "install")
        elif ours(p):
            out[n] = ("ours", "rewrite") if not c.exists() else ("ours, chained to " + c.name, "rewrite")
        elif c.exists():
            out[n] = ("not ours, and " + c.name + " is taken", "refuse")
        else:
            out[n] = ("not ours", "chain it to " + c.name)
    return out


def text_of(name, py, vault):
    for v in (py, vault):
        if "'" in str(v):
            raise SystemExit(f"a quote in {v}: pass another --python or --vault")
    return SH.format(mark=MARK, name=name, chain=CHAIN, py=Path(py).as_posix(), vault=Path(vault).as_posix())


def show(hooks, vault, py, steps):
    print(f"hooks: {hooks}\nvault: {vault}\npython: {py}\nlog: {vault.parent / 'vault-hook.log'}")
    for n, (now, do) in steps.items():
        print(f"{n}: {now}, would {do}")


def install(a):
    hooks, vault = where(a.repo, a.vault)
    steps = plan(hooks)
    show(hooks, vault, a.python, steps)
    bad = [n for n, (_, do) in steps.items() if do == "refuse"]
    if bad:
        print(f"refused: {', '.join(bad)}: move the {CHAIN} file away first; nothing changed")
        return 1
    hooks.mkdir(parents=True, exist_ok=True)
    for n, (_, do) in steps.items():
        if do.startswith("chain"):
            os.replace(hooks / n, hooks / (n + CHAIN))          # a rename keeps its mode and bytes
        p = hooks / n
        p.write_bytes(text_of(n, a.python, vault).encode("utf-8"))
        p.chmod(0o755)
    print("installed: " + ", ".join(NAMES))
    return 0


def remove(a):
    hooks, _ = where(a.repo, None)
    gone = []
    for n in NAMES:
        p, c = hooks / n, hooks / (n + CHAIN)
        if ours(p):
            p.unlink()
            if c.exists():
                os.replace(c, p)
            gone.append(n)
    print("removed: " + ", ".join(gone) if gone else "not installed: nothing changed")
    return 0


def log_to(log, lines):
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.is_file() and log.stat().st_size > LOG_KB * 1024:
        keep = log.read_bytes()[-LOG_KB * 512:]
        log.write_bytes(keep[keep.find(b"\n") + 1:])
    with log.open("a", encoding="utf-8") as f:
        f.write("".join(l.rstrip("\n") + "\n" for l in lines))


def run(a):
    """In git's hook, on main: build HEAD if it changed. Never raises, never fails git; one stderr line on a miss or error."""
    vault = Path(a.vault)
    log, stamp = vault.parent / "vault-hook.log", time.strftime("%Y-%m-%d %H:%M:%S")
    out, err, say = io.StringIO(), io.StringIO(), None
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import vaultify
        with redirect_stdout(out), redirect_stderr(err):
            vaultify.main(["--repo", ".", "--rev", "HEAD", "--vault", str(vault), "--if-changed"])
        res = json.loads(out.getvalue().strip().splitlines()[-1])
        bad = res.get("misses", 0)
        word = "skip" if res.get("skipped") else "miss" if bad else "ok"
        lines = [f"{stamp} {a.name} {res['rev']} {word} " + " ".join(
            f"{k}={res[k]}" for k in ("written", "deleted", "kept_edited", "misses", "seconds") if k in res)]
        lines += ["  " + l for l in err.getvalue().splitlines()]
        if bad:
            say = f"vault: {res.get('misses', 0)} miss: see {log} (the librarian fixes the doc row or vaultify)"
    except BaseException as e:                              # SystemExit from git included: a hook never fails git
        if isinstance(e, KeyboardInterrupt):
            return 0
        lines = [f"{stamp} {a.name} error {type(e).__name__}: {e}"] + ["  " + l for l in (
            err.getvalue() + traceback.format_exc()).splitlines()]
        say = f"vault: error {type(e).__name__}: {str(e)[:80]}: see {log}"
    try:
        log_to(log, lines)
    except OSError as e:
        say = (say or "vault: ok") + f" (no log: {e})"
    if say:
        print(say, file=sys.stderr)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Git hooks that rebuild the vault when main moves (install, remove, print).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd, what in (("install", "write post-merge and post-commit (a hook not ours is chained)"),
                      ("remove", "delete ours, put a chained hook back"), ("print", "show the plan; write nothing"),
                      ("run", "what the hook runs (on main only)")):
        p = sub.add_parser(cmd, help=what)
        p.add_argument("--repo", default=".", help="a path in the repository (default: here)")
        if cmd != "remove":
            p.add_argument("--vault", required=cmd == "run", help="the vault (default <main tree>/.work/vault)")
        if cmd in ("install", "print"):
            p.add_argument("--python", default=sys.executable, help="the python the hook runs (default: this one)")
        if cmd == "run":
            p.add_argument("name", choices=NAMES)
    a = ap.parse_args(argv)
    if a.cmd == "print":
        hooks, vault = where(a.repo, a.vault)
        show(hooks, vault, a.python, plan(hooks))
        return 0
    return {"install": install, "remove": remove, "run": run}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
