"""Add one entry to docs/MERGES.md, newest on top, without reading the file into anyone's context.

    python -I tools/work/merge-log.py --batch <id> --sha <sha8> --what "<what merged>" --up "<redeployed?>" \
        --try "<what to try>" --from '`docs/specs/done/<id>.md`'

The entry goes right after the marker line in the file's header. The file stays UTF-8 with LF. Nothing is committed.
`tools/ci/gate.py ship` calls it; by hand only when a merge is finished without the gate's ship.
`--from` names why the batch was made (`merge-cause`): a backticked batch id, a backticked `docs/...md` path, or
`Owner YYYY-MM-DD: "<quote>"` (tools/work/cause.py); with none of them the entry is refused in one line. Put it in
single quotes: in double quotes a backtick is PowerShell's escape and bash's command substitution.
The entry gets a `- **Recall:** ..` bullet: `tools/kmap/kmap.py recall <sha> --batch <id> --log`, the map at the
merge's first parent against the files it changed, also logged in `.work/kmap/recall.jsonl` with its miss
candidates. Only when the MERGES file sits in a git checkout and the tool is there; `--no-recall` leaves it out.
A failed measure is a `not measured (..)` bullet, never a refused entry.
"""
import argparse
import datetime
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # `python -I` leaves it off
from cause import refs_of  # noqa: E402

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "docs" / "MERGES.md"
MARKER = "<!-- merge-log.py adds each new entry below this line, newest first -->"
KMAP = Path(__file__).resolve().parents[1] / "kmap" / "kmap.py"
RECALL_TIMEOUT = 30   # `kmap recall --log` took 2.0 s cold, the slowest cold map build seen 7 s


def recall_of(merges: Path, batch: str, sha: str) -> str:
    """The `Recall:` bullet's text; "" when the MERGES file is in no git checkout or the tool is missing."""
    repo = merges.resolve().parents[1]
    if not KMAP.is_file() or not (repo / ".git").exists():
        return ""
    try:
        got = subprocess.run([sys.executable, "-I", "-X", "utf8", str(KMAP), "--root", str(repo), "recall", sha,
                              "--batch", batch, "--log"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=RECALL_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return f"not measured ({type(e).__name__})"
    lines = got.stdout.splitlines()
    sys.stdout.write("".join(ln + "\n" for ln in lines if ln.startswith("RECALL ")))
    return next((ln[len("MERGES: "):] for ln in lines if ln.startswith("MERGES: ")), "") or \
        f"not measured (kmap exit {got.returncode})"


def one_line(text: str) -> str:
    return " ".join(text.split())


def entry(when: str, batch: str, sha: str, what: str, up: str, try_: str, from_: str = "", recall: str = "") -> str:
    return (f"### {one_line(when)} · {one_line(batch)} · {one_line(sha)}\n"
            f"- **What:** {one_line(what)}\n- **Up:** {one_line(up)}\n- **Try:** {one_line(try_)}\n"
            f"- **From:** {one_line(from_)}\n" + (f"- **Recall:** {one_line(recall)}\n" if recall else "") + "\n")


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
    ap.add_argument("--from", dest="from_", default="",
                    help='why it was made: `<batch id>`, `docs/...md` or Owner YYYY-MM-DD: "<quote>" (required)')
    ap.add_argument("--when", help="YYYY-MM-DD HH:MM; default: the local clock now")
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE, help=f"default: {DEFAULT_FILE}")
    ap.add_argument("--no-recall", action="store_true", help="no `Recall:` bullet (tools/kmap/kmap.py recall)")
    a = ap.parse_args(argv)
    if not refs_of(a.from_):
        sys.stderr.write(f"NO CAUSE: --from names no `batch id`, `docs/...md` path or Owner YYYY-MM-DD: \"quote\" "
                         f"({one_line(a.from_)[:60] or 'not given'}); nothing added\n")
        return 2
    when = a.when or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    recall = "" if a.no_recall else recall_of(a.file, a.batch, a.sha)
    add(a.file, entry(when, a.batch, a.sha, a.what, a.up, a.try_, a.from_, recall))
    sys.stdout.write(f"added {a.batch} {a.sha} to {a.file} (not committed)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
