"""The fixtures kmap's tests use, stdlib only: a git repository built once per run (`shared`), a fresh `tmp_path` per
test, `monkeypatch.setattr`, and `cases(...)` for a parametrized test (each case a subTest). `suite(globals())` makes
the module's `test_*` functions one TestCase, each given the fixtures it names; a plain `assert` fails as in unittest."""
import atexit
import inspect
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]              # tools/
KMAP = TOOLS / "kmap" / "kmap.py"
if str(KMAP.parent) not in sys.path:
    sys.path.insert(0, str(KMAP.parent))
GIT = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]
FIXTURES = ("repo", "tmp_path", "monkeypatch")
_shared = {}


def rmtree(p):
    """git leaves read-only objects: Windows refuses to delete them until the bit is cleared."""
    def fix(func, path, _):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if Path(p).exists():
        shutil.rmtree(p, **({"onexc": fix} if sys.version_info >= (3, 12) else {"onerror": fix}))


def tmp():
    return Path(tempfile.mkdtemp(prefix="kmap-t-"))


class Repo:
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(["git", "-C", str(self.path), *GIT, *args], capture_output=True, text=True,
                              encoding="utf-8", check=True, env=env, timeout=60).stdout.strip()

    def write(self, rel, text):
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="")
        return p

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def branch(self, name):
        self.git("checkout", "-q", "-b", name)


def shared(build):
    """`build(top)`'s result, made once per run in its own folder (deleted at exit)."""
    if build not in _shared:
        top = tmp()
        atexit.register(rmtree, top)
        _shared[build] = build(top)
    return _shared[build]


class Patch:
    def __init__(self):
        self.undo = []

    def setattr(self, obj, name, value):
        self.undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def close(self):
        for obj, name, value in reversed(self.undo):
            setattr(obj, name, value)


def cases(*rows):
    """`@cases((a, b), (c, d))`: the test's own parameters, after its fixtures, once per row."""
    def mark(fn):
        fn.cases = rows
        return fn
    return mark


def suite(ns, repo=None):
    def method(fn):
        params = list(inspect.signature(fn).parameters)
        own = [p for p in params if p not in FIXTURES]

        def test(self):
            for row in getattr(fn, "cases", [()]):
                with self.subTest(**{k: repr(v)[:60] for k, v in zip(own, row)}):
                    where, patch = tmp(), Patch()
                    try:
                        give = {"tmp_path": where, "monkeypatch": patch}
                        if "repo" in params:
                            give["repo"] = shared(repo)
                        fn(**{p: give[p] for p in params if p in FIXTURES}, **dict(zip(own, row)))
                    finally:
                        patch.close()
                        rmtree(where)
        test.__doc__ = fn.__doc__
        return test
    return type("Tests", (unittest.TestCase,), {n: method(f) for n, f in ns.items()
                                                 if n.startswith("test_") and inspect.isfunction(f)})
