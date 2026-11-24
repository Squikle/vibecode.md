#!/usr/bin/env python3
"""A 3-way union merge of one doc file with its line endings normalised:
`python -I -X utf8 tools/work/eolmerge.py <file> <base-rev> <theirs-rev>`.

For a conflict in an append-only doc (a log, a table both sides added rows to) where one side has CRLF and the
other LF, so git sees every line as changed. Ours is HEAD; each side is read from git with CRLF made LF, merged with
`git merge-file --union` (both sides' lines kept, no markers), and the result written back with HEAD's line ending.
Prints `<file> <n> lines, <u> unique`: a drop in unique lines means a row was lost. Never for code: a union of two
code edits is not a merge. Unknown arguments are an error."""
import subprocess
import sys
import tempfile
from pathlib import Path


def show(rev: str, path: str) -> bytes:
    done = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True)
    if done.returncode != 0:
        raise SystemExit(f"eolmerge: git show {rev}:{path}: {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout


def main(argv: list[str]) -> int:
    if len(argv) != 3 or any(a.startswith("-") for a in argv):
        print(__doc__.strip().splitlines()[1], file=sys.stderr)
        return 2
    path, base, theirs = argv
    ours = show("HEAD", path)
    crlf = b"\r\n" in ours
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for name, raw in (("ours", ours), ("base", show(base, path)), ("theirs", show(theirs, path))):
            f = Path(tmp) / name
            f.write_bytes(raw.replace(b"\r\n", b"\n"))
            files.append(str(f))
        merged = subprocess.run(["git", "merge-file", "-p", "--union", *files], capture_output=True).stdout
    if crlf:
        merged = merged.replace(b"\n", b"\r\n")
    Path(path).write_bytes(merged)
    lines = merged.decode("utf-8", "replace").splitlines()
    print(f"{path} {len(lines)} lines, {len(set(lines))} unique")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
