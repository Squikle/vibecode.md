"""The backlog's order, kept in one place: an owner's word about what comes first goes into docs/BACKLOG.md in the
same turn, stamped and logged, and `check` finds a row that moved against the newest word.

Usage: python -I -X utf8 tools/work/order.py word "<the owner's words, verbatim>" --rows <id>[,<id>...]
                                            (--top | --after <id> | --before <id> | --hold | --drop)
                                            [--needs "<role>: <what>"] [--status "<text>"] [--dry-run]
       python -I -X utf8 tools/work/order.py check
  `word` moves the rows (in the order given) to the top, after or before a row, holds them in place or drops them;
  `--needs` alone holds them (`held, needs product: <what>`); `--status` alone sets their status in place (to lift a
  hold: `--status "ready to build"`), or with a move the status they get. Each
  moved or held row's status ends `(owner YYYY-MM-DD)`; the `#` column is renumbered; every other row keeps its
  line byte for byte. Each named row must be in the table exactly once, else nothing is written (exit 1). It appends
  `- YYYY-MM-DD hh:mm WORD rows=<ids> move=<how> quote="<words>"` to docs/log/order.md (a dropped or held row's old
  status as `was=`) and prints the diff; you commit both files by path. `--dry-run` prints the diff only.
  `check`: a row twice, the `#` column out of order, a stamped row with no WORD line, and a row whose newest WORD no
  longer holds (a `top` row under an unstamped one, an `after`/`before` broken, a held row not held, a dropped row
  back). Exit 1 on any; the coordinator runs it after another session's backlog commit and on each PLANNED.
`--root <dir>` (default: two folders up), `--backlog <file>`, `--log <file>` for tests. Unknown arguments: exit 2.
"""
import argparse
import difflib
import re
import sys
import time
from pathlib import Path

TICK = re.compile(r"`([^`\n]+)`")
STAMP = re.compile(r"\s*\(owner \d{4}-\d\d-\d\d\)\s*$")
CELL = re.compile(r"(?<!\\)\|")
WORD = re.compile(r"^(?:[-*] +)?(\d{4}-\d\d-\d\d(?: \d\d:\d\d)?) WORD (.*)$")
KV = re.compile(r'([a-z]+)=("[^"]*"|\S+)')
LOG_HEAD = ("# Order words\n\nOne line per owner word about the build order, written by `tools/work/order.py word`;"
            " never edited by hand. BACKLOG's order is the build order; this log says why.\n\n")


class Backlog:
    def __init__(self, path):
        self.path = path
        raw = path.read_bytes().decode("utf-8")
        self.eol = "\r\n" if "\r\n" in raw else "\n"
        self.lines = raw.split(self.eol)
        self.start = self.header = None
        for i, line in enumerate(self.lines):
            cells = self.cells(line)
            if cells and "batch" in [c.lower() for c in cells] and "status" in [c.lower() for c in cells]:
                self.header, self.start = [c.lower() for c in cells], i + 2
                break
        if self.header is None:
            raise SystemExit(f"order: no table with Batch and Status columns in {path}")
        self.end = self.start
        while self.end < len(self.lines) and self.lines[self.end].lstrip().startswith("|"):
            self.end += 1
        self.rows = self.lines[self.start:self.end]
        self.col = {h: i for i, h in enumerate(self.header)}

    @staticmethod
    def cells(line):
        s = line.strip()
        return [c.strip() for c in CELL.split(s.strip("|"))] if s.startswith("|") else None

    def row_id(self, line):
        c = self.cells(line)
        ids = TICK.findall(c[self.col["batch"]]) if c and len(c) > self.col["batch"] else []
        return ids[0] if ids else None

    def ids(self):
        return [self.row_id(r) for r in self.rows]

    def status(self, line):
        c = self.cells(line)
        return c[self.col["status"]] if c and len(c) > self.col["status"] else ""

    def with_status(self, line, status):
        c = self.cells(line)
        c[self.col["status"]] = status
        return "| " + " | ".join(c) + " |"

    def renumber(self):
        if "#" not in self.col:
            return
        for i, line in enumerate(self.rows):
            c = self.cells(line)
            if c[self.col["#"]] != str(i + 1):
                c[self.col["#"]] = str(i + 1)
                self.rows[i] = "| " + " | ".join(c) + " |"

    def text(self):
        return self.eol.join(self.lines[:self.start] + self.rows + self.lines[self.end:])


def words(log):
    """[(when, {key: value})] of each WORD line, oldest first."""
    out = []
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            if m := WORD.match(line.strip()):
                out.append((m.group(1), {k: v.strip('"') for k, v in KV.findall(m.group(2))}))
    return out


def word(a, backlog, log):
    b = Backlog(backlog)
    want = [r.strip() for r in a.rows.split(",") if r.strip()]
    ids = b.ids()
    bad = [r for r in want if ids.count(r) != 1] + ([a.after or a.before] if (a.after or a.before)
                                                     and ids.count(a.after or a.before) != 1 else [])
    if bad:
        print("order: not in docs/BACKLOG.md exactly once: " + ", ".join(f"`{r}` ({ids.count(r)}x)" for r in bad))
        return 1
    anchor = a.after or a.before
    if anchor in want:
        print(f"order: `{anchor}` is one of the rows it moves")
        return 2
    today = time.strftime("%Y-%m-%d")
    move = ("top" if a.top else f"after:{a.after}" if a.after else f"before:{a.before}" if a.before else
            "drop" if a.drop else "hold" if a.hold or not a.status else "status")
    picked = {r: b.rows[ids.index(r)] for r in want}
    was = {r: STAMP.sub("", b.status(line)) for r, line in picked.items()}
    for r, line in picked.items():
        status = a.status or (f"held, needs {a.needs}" if a.needs else "held" if move == "hold" else was[r])
        picked[r] = b.with_status(line, f"{status} (owner {today})")
    if move in ("hold", "status"):
        b.rows = [picked.get(b.row_id(x), x) for x in b.rows]
    else:
        rest = [x for x in b.rows if b.row_id(x) not in picked]
        moved = [] if move == "drop" else [picked[r] for r in want]
        at = 0 if move == "top" else len(rest) if move == "drop" else \
            next(i for i, x in enumerate(rest) if b.row_id(x) == anchor) + (1 if a.after else 0)
        b.rows = rest[:at] + moved + rest[at:]
    b.renumber()
    old, new = backlog.read_bytes().decode("utf-8"), b.text()
    diff = "\n".join(difflib.unified_diff(old.split(b.eol), new.split(b.eol), "a/" + backlog.name, "b/" + backlog.name,
                                          lineterm="", n=0))
    quote = re.sub(r"\s+", " ", a.quote.replace('"', "'")).strip()[:300]
    line = f"- {time.strftime('%Y-%m-%d %H:%M')} WORD rows={','.join(want)} move={move} quote=\"{quote}\""
    if move in ("drop", "hold") or a.needs:
        line += " was=\"" + "; ".join(was[r].replace('"', "'") for r in want) + "\""
    if a.needs:
        line += f" needs=\"{a.needs.replace(chr(34), chr(39))}\""
    print(diff or "(no change in the table)")
    print(("would log: " if a.dry_run else "logged: ") + line)
    if a.dry_run:
        return 0
    backlog.write_bytes(new.encode("utf-8"))
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8", newline="\n") as f:
        if log.stat().st_size == 0:
            f.write(LOG_HEAD)
        f.write(line + "\n")
    print(f"commit by path: {backlog.name} and {log.name}")
    return 0


def check(backlog, log):
    b = Backlog(backlog)
    ids, reds = b.ids(), []
    for r in sorted({r for r in ids if r and ids.count(r) > 1}):
        reds.append(f"`{r}` is in the table {ids.count(r)} times")
    if "#" in b.col:
        nums = [b.cells(x)[b.col["#"]] for x in b.rows]
        if nums != [str(i + 1) for i in range(len(nums))]:
            reds.append("the # column is not 1..n in order (renumber)")
    newest = {}
    for when, kv in words(log):
        for r in kv.get("rows", "").split(","):
            if r:
                newest[r] = kv.get("move", "")
    pos = {r: i for i, r in enumerate(ids) if r}
    stamped = {r for r, x in zip(ids, b.rows) if r and STAMP.search(b.status(x))}
    for r in sorted(stamped - set(newest)):
        reds.append(f"`{r}` is stamped by the owner but no WORD line names it")
    for r, move in sorted(newest.items()):
        if move == "drop":
            if r in pos:
                reds.append(f"`{r}` was dropped by the owner's word and is back")
            continue
        if r not in pos:
            continue  # merged and removed: its word is done
        how, _, other = move.partition(":")
        if how == "top":
            above = [x for x in ids[:pos[r]] if x not in stamped]
            if above:
                reds.append(f"`{r}` came first by the owner's word, now under unstamped `{above[0]}`")
        elif how in ("after", "before") and other in pos:
            if (pos[r] < pos[other]) if how == "after" else (pos[r] > pos[other]):
                reds.append(f"`{r}` goes {how} `{other}` by the owner's word, now on the wrong side")
        elif how == "hold" and not b.status(b.rows[pos[r]]).lower().startswith("held"):
            reds.append(f"`{r}` is held by the owner's word, its status says `{b.status(b.rows[pos[r]])}`")
    for x in reds:
        print("RED  " + x)
    print(f"order check: {len(reds)} red ({len(ids)} rows, {len(newest)} rows with a word)")
    return 1 if reds else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--backlog", default="docs/BACKLOG.md")
    ap.add_argument("--log", default="docs/log/order.md")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("word", allow_abbrev=False)
    w.add_argument("quote")
    w.add_argument("--rows", required=True)
    g = w.add_mutually_exclusive_group()
    g.add_argument("--top", action="store_true")
    g.add_argument("--after")
    g.add_argument("--before")
    g.add_argument("--hold", action="store_true")
    g.add_argument("--drop", action="store_true")
    w.add_argument("--needs", default="")
    w.add_argument("--status", default="")
    w.add_argument("--dry-run", action="store_true")
    sub.add_parser("check", allow_abbrev=False)
    a = ap.parse_args(argv)
    root = Path(a.root)
    backlog, log = root / a.backlog, root / a.log
    if not backlog.is_file():
        print(f"order: no {backlog}")
        return 1
    if a.cmd == "check":
        return check(backlog, log)
    if not (a.top or a.after or a.before or a.hold or a.drop or a.needs or a.status):
        ap.error("word needs one of --top, --after, --before, --hold, --drop, --needs or --status")
    if (a.drop and (a.needs or a.status)) or (a.status and (a.hold or a.needs)):
        ap.error("--drop takes no --needs or --status; --status lifts a hold, so not with --hold or --needs")
    return word(a, backlog, log)


if __name__ == "__main__":
    sys.exit(main())
