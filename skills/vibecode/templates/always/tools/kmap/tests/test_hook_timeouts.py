"""Guard: every subprocess on the git hook, vault build and merge-log path has a timeout, so a hang can never hold a
commit or a ship. Scans `tools/kmap/`, `tools/vault/` and `tools/work/merge-log.py` for `subprocess.run`,
`check_output`, `call`, `check_call` and `.communicate(...)` without `timeout=` (a `**kwargs` call counts as without);
an exception goes in ALLOW with its reason. A manual sweep missed one before this test.

python -I -X utf8 -m unittest discover -s tools/kmap/tests      (stdlib only)"""
import ast
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
PATHS = ["kmap/*.py", "vault/*.py", "work/merge-log.py"]      # the vault hook runs on every main commit
CALLS = {"run", "check_output", "call", "check_call"}
ALLOW: dict[str, str] = {}      # "path:function" -> why it may wait without bound


def untimed_in(rel, text):
    out = []
    tree = ast.parse(text)
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] + [tree]:
        for call in ast.walk(fn):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                f = call.func
                owner = f.value.id if isinstance(f.value, ast.Name) else ""
                name = getattr(fn, "name", "<module>")
                if ((owner == "subprocess" and f.attr in CALLS) or f.attr == "communicate") \
                        and not any(k.arg == "timeout" for k in call.keywords) and f"{rel}:{name}" not in ALLOW:
                    out.append(f"{rel}:{call.lineno} {f.attr}")
    return sorted(set(out))


class HookTimeouts(unittest.TestCase):
    def test_every_subprocess_on_the_hook_path_has_a_timeout(self):
        found = [x for pattern in PATHS for path in sorted(TOOLS.glob(pattern))
                 for x in untimed_in(path.relative_to(TOOLS).as_posix(), path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "add timeout= (a hang holds the git hook or the ship), or an ALLOW entry")

    def test_the_scan_sees_kwargs_and_communicate(self):
        self.assertEqual(untimed_in("x.py", "import subprocess\ndef f(kw):\n    subprocess.run(['git'], **kw)\n"
                                    "    subprocess.Popen(['git']).communicate()\n"
                                    "    subprocess.check_output(['git'], timeout=5)\n"),
                         ["x.py:3 run", "x.py:4 communicate"])
