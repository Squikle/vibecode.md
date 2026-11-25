"""Resolve every conflict in one file by keeping both sides, ours first: for append-only files (a log, a ledger, a
list of rows) where both branches added lines. Never for code or prose that one side changed.

Usage: python -I -X utf8 tools/work/union.py <file>   then check the result, `git add <file>`.

The file keeps its own line endings. A file with no conflict is left as it is.
"""
import sys


def union(s: str) -> str:
    while "<<<<<<< " in s:
        a = s.index("<<<<<<< ")
        a2 = s.index("\n", a) + 1
        mid = s.index("=======\n", a2)
        c = s.index(">>>>>>> ", mid)
        c2 = s.index("\n", c) + 1
        s = s[:a] + s[a2:mid] + s[mid + 8:c] + s[c2:]
    return s


def main(path: str) -> None:
    raw = open(path, "rb").read()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    s = raw.decode("utf-8").replace("\r\n", "\n")
    open(path, "w", encoding="utf-8", newline=nl).write(union(s))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[3])
    main(sys.argv[1])
