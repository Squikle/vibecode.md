"""The knowledge map's constants and the small readers every part shares: git, markdown tables and sections, the
`Links:` grammar, id-shaped tokens. Standard library only."""
import os
import re
import subprocess
from pathlib import Path

SCHEMA = 2
KEEP_DBS = 8
ALPHA, THETA = 0.7, 0.01        # decay per hop; a share under THETA stops
WEAK = 0.05                     # `path`: a chain scored under this is called weak
HISTORY = 1500                  # first-parent commits read for implemented-in and co-change (main had 972 on 2026-11-27)
CO_MAX, CO_SUPPORT, CO_CONF = 25, 3, 0.5
HUB_MIN = 30                    # a degree over max(HUB_MIN, p99) is a hub warning
MAX_BYTES = 1_000_000           # a bigger file is a node, never parsed
BUDGET = {0: 40, 1: 40, 2: 120, 3: 40}
L3_SYMBOLS, L3_WHOLE = 3, 60    # level 3: at most 3 symbols' line ranges per file; a file of 60 lines or fewer is read whole
LIST_FLOOR, LIST_COMPACT = 0.05, 150   # an area or module whose best relation is under 0.05 gets the inventory hint
#                                        (27 of 30 areas' best is under it, max 0.096; every module's 0.13+, specs' p10
#                                        0.059: measured 2026-11-28); `list` past 150 items: names only
RECALL_K = 20                   # `recall`: the top 20 files of the batch's topic against the files its merge changed
FOUND_MIN, FOUND_EXPIRY = 3, 10  # a candidate pair with 3 independent evidences is a `found` link; 10 merges touching
#                                  a file end with no new evidence expire it
REVIEW_MAX, REVIEW_DAYS = 20, 7  # `candidates --review` shows 20 pairs; the heartbeat asks for a review past 20 waiting
#                                  pairs, or 7 days since the last review (or the oldest waiting evidence)
# Every merge window counts the merges that touched the pair's own file ends (`extra.merged`), never main's merges
# at large: a busy main lands ~137 first-parent commits a day, so a global count expires in hours (measured 2026-11-28).
COLLECT_DAYS, COLLECT_MERGES = 30, 20   # a collecting co-change pair leaves candidates.jsonl at merge time after 20
#                                         merges that touched one end without the other, or 30 days with no evidence:
#                                         on one project's 336 merges (11 days), the one-sided merges between a
#                                         pair's co-changes are p90 13, p95 18, max 47; 20 keeps 96 % of the real gaps
FILE_MERGES = 40                        # merge times kept per file: covers 20 one-sided merges of two ends
ID_MIN = 6                              # a review id: at least 6 hex, longer only where two pairs share a prefix
REJECT_DAYS, REJECT_MERGES = 30, 20   # a rejection is a snapshot: back in review after 20 merges that changed either
#                                       end, 30 days at most, at once when an end's fingerprint
#                                       changes or 2 new mechanical evidences (or 1 and a self-report) arrive
CODE = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".css", ".html", ".sh", ".ps1", ".sql"}
SKIPPED = {"node_modules", "dist", "build", "__pycache__", ".venv", "venv", ".git", ".work", ".bt", "e2e-results",
           "test-results", "playwright-report", "coverage", ".workspaces"}
LINK_TYPES = {"depends-on", "applied-by", "applies", "shares-ui", "supersedes", "conflicts", "part-of", "not"}
# id prefixes; a test file is `file:` of kind `test`. `feat:` a feature (a project may add them), `q:` a QUESTIONS row
KINDS = ("mod", "spec", "dec", "area", "feat", "q", "file")
HUBBY = ("file", "dec", "area", "q")   # kinds damped by their degree
# base weight per edge type; CURATED links are written by a role, EXTRACTED parsed, STAT counted
EDGE = {"depends-on": 1.0, "applied-by": 0.9, "applies": 0.9, "supersedes": 1.0, "conflicts": 0.8, "shares-ui": 0.5,
        "part-of": 1.0, "same-files-as": 0.6, "specified-in": 1.0, "implemented-in": 0.9, "touches-file": 0.8,
        "binds": 0.8, "cites": 0.7, "amends": 1.0, "maps": 0.6, "imports": 0.7, "tested-by": 0.7, "mentions": 0.4,
        "in-feature": 0.6, "asks-about": 0.4, "co-changes": 0.5, "found": 0.5,
        "accepted": 0.8}   # found: half a link, made by code; accepted: a candidate the architect took in review
REVERSE = {"depends-on": "needed-by", "applied-by": "applies", "applies": "applied-by", "supersedes": "superseded-by",
           "part-of": "has", "specified-in": "specifies", "implemented-in": "implements", "touches-file": "touched-by",
           "binds": "bound-by", "cites": "cited-by", "amends": "amended-by", "maps": "mapped-in",
           "imports": "imported-by", "tested-by": "tests", "mentions": "mentioned-by", "in-feature": "has-batch",
           "asks-about": "asked-in"}
BACK = {"implemented-in": 0.6, "touches-file": 0.6, "mentions": 0.6, "imports": 0.8, "asks-about": 0.5}
ROLES = {   # the link types each role needs, weighed up or down (`--role`)
    "product": {"depends-on": 1.5, "same-files-as": 1.2, "cites": 1.5, "binds": 1.2, "mentions": 1.3, "part-of": 1.3,
                "conflicts": 2.0, "in-feature": 1.5, "asks-about": 1.3, "implemented-in": 0.6, "imports": 0.3,
                "co-changes": 0.5},
    "architect": {"imports": 1.5, "co-changes": 1.5, "maps": 1.3, "binds": 1.3, "implemented-in": 1.2,
                  "depends-on": 1.2},
    "plan-review": {"depends-on": 1.5, "cites": 1.5, "binds": 1.3, "conflicts": 2.0, "mentions": 1.2, "maps": 1.2,
                    "asks-about": 1.3},
    "builder": {"implemented-in": 1.5, "touches-file": 1.5, "tested-by": 1.5, "imports": 1.2, "binds": 1.2},
    "reviewer": {"binds": 1.5, "tested-by": 1.5, "imports": 1.2, "cites": 1.2},
    "librarian": {"mentions": 1.5, "cites": 1.5, "binds": 1.3, "supersedes": 1.3, "amends": 1.3, "asks-about": 1.5},
    "designer": {"part-of": 1.5, "shares-ui": 2.0, "touches-file": 1.3, "maps": 1.2, "imports": 0.6},
}
MARK = {"CURATED": "!", "STAT": "*", "FOUND": "~"}
TICK = re.compile(r"`([^`\n]{1,160})`")
H1 = re.compile(r"^#\s+(.+?)\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
HEADER = re.compile(r"^(Status|Depends on|Same files as|Tech|Links|Review):\s*(.*?)\s*$")
RULE = re.compile(r"^\s*\d+\.\s+\*\*(.+?)\*\*")
CITE = re.compile(r"\b(?:decisions?|DECISIONS)\s+#?(\d+(?:\s*(?:,|and|or|&|to)\s*#?\d+)*)", re.I)
AMENDS = re.compile(r"\b(?:amends|replaces|supersedes)\s+(?:decision\s+)?#?(\d+)", re.I)
REPLACED = re.compile(r"^\s*(?:\*\*)?\s*\u2192\s*(\d+)")      # DECISIONS: a row that starts `→ 12` is replaced by 12
TS_IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)['"]([^'"\n]+)['"]""", re.M)
SEP = re.compile(r":?-{2,}:?")
ID_SHAPE = re.compile(r"(?:(?:spec|mod|dec|area|feat):)?[a-z0-9][a-z0-9-]*")
SPEC_PATH = re.compile(r"^docs/specs/(?:done/)?([\w-]+)\.md$")
TITLE_IDS = re.compile(r"\s*\((?:`[^`]+`[,;\s]*(?:and\s+)?)+\)\s*$")   # a spec's H1 ends `(`a`, `b`)`
# `docs/MERGES.md`: `### date time · batch · sha` and its `- **Key:** value` bullets (`tools/work/merge-log.py`)
ENTRY = re.compile(r"^### (\d{4}-\d\d-\d\d) (\d\d:\d\d) · ([\w-]+) · (\w+)\n((?:- .*\n?)*)", re.M)
# A spec's Try: ONE line `**Try:** <one clause>` (`docs/PLANNING.md`); `-` or a dash is none
SPEC_TRY = re.compile(r"^\*\*Try:\*\*[ \t]*(.*?)[ \t\r]*$", re.M)      # a CRLF file leaves a `\r` before the `$`


def run_git(root, *args):
    try:
        r = subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=off", *args], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):   # a 1,500-commit log takes 0.3 s; a hang is no answer
        return None
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def git_top(root):
    """True when `root` is the top of a git checkout: a folder inside another repository builds from its files."""
    top = run_git(root, "rev-parse", "--show-toplevel")
    return bool(top) and Path(top.strip()).resolve() == Path(root).resolve()


def is_test(path):
    name = path.rsplit("/", 1)[-1]
    return ".test." in name or ".spec." in name or name.startswith("test_") or name.endswith("_test.py") \
        or name == "conftest.py"


def table_rows(lines):
    """(line number, header cells lower-cased, cells) for each body row of each markdown table."""
    header = None
    for number, line in enumerate(lines, 1):
        s = line.strip()
        if not s.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", s.strip("|"))]
        if header is None:
            header = [c.lower() for c in cells]
        elif not all(SEP.fullmatch(c) for c in cells if c):
            yield number, header, cells


def sections(lines):
    """[title, first line, last line] for each `##`+ heading, outside code fences."""
    out, fenced = [], False
    for number, line in enumerate(lines, 1):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and (m := HEADING.match(line)) and len(m.group(1)) >= 2:
            if out:
                out[-1][2] = number - 1
            out.append([m.group(2), number, len(lines)])
    return out


def parse_links(value):
    """`<type> `id` (reason)` items joined by ` · ` or `;` -> ([(type, id, reason)], [bad items])."""
    items, bad = [], []
    for part in re.split(r"\s+\u00b7\s+|\s*;\s*", value.strip()):
        part = part.strip()
        if not part or part.lower().rstrip(".") in ("none", "-", "nothing"):
            continue
        m = re.match(r"([a-z][a-z-]*)\s+`", part)
        ids = TICK.findall(part)
        if not m or not ids or m.group(1) not in LINK_TYPES:
            bad.append(part)
            continue
        reason = re.search(r"\(([^)]*)\)\s*$", part)
        items += [(m.group(1), i, reason.group(1) if reason else "") for i in ids]
    return items, bad


def merges(text):
    """MERGES' entries, newest first: [{batch, sha, date, what}]."""
    out = []
    for m in ENTRY.finditer(text):
        date, tm, bid, sha, body = m.groups()
        what = re.search(r"^- \*\*What:\*\* (.*)$", body, re.M)
        out.append({"batch": bid, "sha": sha, "date": f"{date}T{tm}", "what": " ".join(what.group(1).split()) if what else ""})
    return out


def spec_try(text):
    """A spec's Try, or "" when it has none, a dash or two lines (one batch, one Try)."""
    got = SPEC_TRY.findall(text)
    return " ".join(got[0].split()) if len(got) == 1 and got[0] not in ("", "-", "\u2013", "\u2014") else ""


def ids_in(text):
    """The id-shaped backticked tokens of a free-text field (`Depends on:`, a row's `Waits for`): a path, a flag or a
    word in capitals there is prose, not a link."""
    return [t for t in TICK.findall(text) if ID_SHAPE.fullmatch(t)]


def walk(root):
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED)
        for name in sorted(files):
            yield Path(folder, name)
