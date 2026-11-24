"""Add one entry to docs/MERGES.md, newest on top, without reading the file into anyone's context.

    python -I tools/work/merge-log.py --batch <id> --sha <sha8> --what "<what merged>" --up "<redeployed?>" --try "<what to try>"

The entry goes right after the marker line in the file's header. The file stays UTF-8 with LF. Nothing is committed.
`tools/ci/gate.py ship` calls it; by hand only when a merge is finished without the gate's ship.
"""
import argparse
import datetime
import sys
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "docs" / "MERGES.md"
MARKER = "<!-- merge-log.py adds each new entry below this line, newest first -->"


def one_line(text: str) -> str:
    return " ".join(text.split())


def entry(when: str, batch: str, sha: str, what: str, up: str, try_: str) -> str:
    return (f"### {one_line(when)} · {one_line(batch)} · {one_line(sha)}\n"
            f"- **What:** {one_line(what)}\n- **Up:** {one_line(up)}\n- **Try:** {one_line(try_)}\n\n")


def add(path: Path, text: str) -> None:
    old = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    at = old.find(MARKER)
    if at < 0:
        raise SystemExit(f"{path}: the marker line is missing: {MARKER}")
    at = old.index("\n", at) + 1
    rest = old[at:].lstrip("\n")
    path.write_bytes((old[:at] + "\n" + text + rest).encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Insert one entry at the top of docs/MERGES.md (never commits).")
    ap.add_argument("--batch", required=True, help="the batch id")
    ap.add_argument("--sha", required=True, help="the merge commit, short or full")
    ap.add_argument("--what", required=True, help="what was merged, one clause")
    ap.add_argument("--up", required=True, help='"yes, redeployed and healthy", "no, docs only" or "no: why"')
    ap.add_argument("--try", dest="try_", required=True, help="what to try, one clause")
    ap.add_argument("--when", help="YYYY-MM-DD HH:MM; default: the local clock now")
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE, help=f"default: {DEFAULT_FILE}")
    a = ap.parse_args(argv)
    when = a.when or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    add(a.file, entry(when, a.batch, a.sha, a.what, a.up, a.try_))
    sys.stdout.write(f"added {a.batch} {a.sha} to {a.file} (not committed)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
