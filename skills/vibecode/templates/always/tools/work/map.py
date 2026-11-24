#!/usr/bin/env python3
"""A file's outline or a folder's exports, read from the source as it is now: run it before reading a file over ~300 lines.

`python tools/work/map.py <file>`: the file's line count, then each top-level declaration with its line number, and a class's
or an interface's methods under it; read the lines you need after it.
`python tools/work/map.py <folder>`: one line per source file under it (`.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.py`; tests, `node_modules` and
caches left out): its line count and its exported names. The job of each file is in `docs/architecture/<area>.md`.

TypeScript and JavaScript are read by pattern (a declaration at the start of a line, a method at two spaces in a class or an
interface), Python by `ast`. It prints to stdout and writes nothing; its output is never committed.
"""
import ast
import re
import sys
from pathlib import Path

SOURCES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".py"}
SKIPPED = {"node_modules", "dist", "__pycache__", ".venv", ".git", "e2e-results", "test-results"}
TOP = re.compile(r"^(export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
                 r"(function\*?|class|interface|type|enum|const|let)\s+([A-Za-z_$][\w$]*)")
MEMBER = re.compile(r"^  (?:(?:private|protected|public|readonly|static|async|abstract|override|get|set)\s+)*"
                    r"(#?[A-Za-z_$][\w$]*)\??\s*[(<]")
NOT_MEMBERS = {"if", "for", "while", "switch", "return", "catch", "super", "await", "throw"}


def is_test(path: Path) -> bool:
    return ".test." in path.name or path.name.startswith("test_") or path.name == "conftest.py"


def ts_outline(text: str) -> list[tuple[int, int, str, bool]]:
    """(line, depth, `kind name`, exported) for each top-level declaration and each method of a class or an interface."""
    found, owner = [], False
    for number, line in enumerate(text.splitlines(), 1):
        if m := TOP.match(line):
            kind = m.group(2).rstrip("*")
            found.append((number, 0, f"{kind} {m.group(3)}", bool(m.group(1))))
            owner = kind in ("class", "interface") and not line.rstrip().endswith("}")
        elif line.startswith("}"):
            owner = False
        elif owner and (m := MEMBER.match(line)) and m.group(1) not in NOT_MEMBERS:
            found.append((number, 1, m.group(1), False))
    return found


def py_outline(text: str) -> list[tuple[int, int, str, bool]]:
    """(line, depth, `kind name`, exported) for each function, class and upper-case constant, and each class's methods."""
    found = []
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "def"
            found.append((node.lineno, 0, f"{kind} {node.name}", not node.name.startswith("_")))
            if isinstance(node, ast.ClassDef):
                found += [(m.lineno, 1, m.name, False) for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found += [(node.lineno, 0, f"const {t.id}", True) for t in targets if isinstance(t, ast.Name) and t.id.isupper()]
    return found


def outline(path: Path) -> tuple[int, list[tuple[int, int, str, bool]]]:
    text = path.read_text(encoding="utf-8-sig")
    return len(text.splitlines()), (py_outline(text) if path.suffix == ".py" else ts_outline(text))


def file_map(path: Path) -> str:
    count, found = outline(path)
    rows = [f"{path.as_posix()}: {count} lines"]
    rows += [f"{line:>5}  {'  ' * depth}{what}" for line, depth, what, _ in found]
    return "\n".join(rows)


def folder_map(folder: Path) -> str:
    rows = []
    for path in sorted(folder.rglob("*")):
        if path.suffix not in SOURCES or is_test(path) or SKIPPED & set(path.parts) or not path.is_file():
            continue
        count, found = outline(path)
        names = [what.split()[-1] for _, depth, what, exported in found if exported and depth == 0]
        rows.append(f"{path.relative_to(folder).as_posix()} {count}: {', '.join(names)}")
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[0], "\nusage: python tools/work/map.py <file|folder>", file=sys.stderr)
        return 2
    target = Path(argv[0])
    if not target.exists():
        print(f"not found: {target}", file=sys.stderr)
        return 1
    print(folder_map(target) if target.is_dir() else file_map(target))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
