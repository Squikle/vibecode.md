"""The docs' start reads and budgets: python -I -X utf8 tools/work/check-docs.py (exit 1 on a red, one size line).

Each role file names what its session reads at its start, in its first ten lines:
  **Start reads** (at most 60 KB): `AGENTS.md`, `docs/PLANNING.md`, `docs/HANDOVER.md#3`
`file#3` counts section `## 3.` only. A red is the librarian's job: cut a listed file or drop it from the list.
A budget is raised only on the owner's word.
"""
import re
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
START_KB = 60  # what a role's start reads may total
MAX_LISTED_KB = 50  # no listed file is bigger
FILE_KB = {  # each file's own budget; a file that does not exist is skipped
    "AGENTS.md": 10, "docs/HANDOVER.md": 30, "docs/ARCHITECTURE.md": 30, "docs/PLANNING.md": 30,
    "docs/workflow/coordinator.md": 12, "docs/workflow/architect.md": 12, "docs/workflow/librarian.md": 12,
    "docs/workflow/product.md": 12, "docs/workflow/plan-review.md": 12, "docs/workflow/telemetry.md": 12,
    "docs/workflow/pregroomer.md": 12, "docs/workflow/lane-brief.md": 6, "docs/workflow/lane-more.md": 12,
    "docs/workflow/coordinator-more.md": 12,
    "docs/workflow/reviewer-brief.md": 12, "docs/workflow/integrator-brief.md": 12,
}
SEARCHED = {"docs/DECISIONS.md", "docs/QUESTIONS.md", "docs/ROADMAP.md", "docs/MEASUREMENTS.md", "docs/MERGES.md",
            "docs/workflow/lane-more.md", "docs/workflow/coordinator-more.md"}  # searched or read by section, never whole
LINE = re.compile(r"\*\*Start reads\*\* \(at most (\d+) KB\):(.*)")


def text_of(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")  # universal newlines: a CRLF checkout measures like LF


def start_reads(role: str):
    for line in text_of(role).splitlines()[:10]:
        if m := LINE.search(line):
            return int(m.group(1)), re.findall(r"`([^`]+)`", m.group(2))
    return None


def bytes_of(path: str) -> int:
    file, _, part = path.partition("#")
    if not part:
        return len(text_of(file).encode())
    taken, inside, fenced = [], False, False
    for line in text_of(file).splitlines(keepends=True):
        fenced ^= line.startswith("```")
        if line.startswith("## ") and not fenced:
            inside = line.startswith(f"## {part}.")
        if inside:
            taken.append(line)
    return len("".join(taken).encode()) if taken else -1


def roles() -> list[str]:
    """Role files: those AGENTS.md section 2 names, and any doc with a start-reads line."""
    agents = text_of("AGENTS.md")
    table = agents[agents.index("## 2."):agents.index("## 3.")]
    named = {m for row in table.splitlines() if row.startswith("| **") for m in re.findall(r"`(docs/[\w/.-]+\.md)`", row.split("|")[2])}
    with_line = {p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").rglob("*.md") if start_reads(p.relative_to(ROOT).as_posix())}
    return sorted(named | with_line)


reds, sizes = [], []
for path, kb in FILE_KB.items():
    if (ROOT / path).is_file():
        size = bytes_of(path)
        sizes.append(f"{PurePosixPath(path).stem} {size / 1024:.1f}/{kb}")
        if size > kb * 1024:
            reds.append(f"{path}: {size / 1024:.1f} KB > {kb} KB")
for role in roles():
    found = start_reads(role)
    if not found:
        reds.append(f"{role}: no `**Start reads** (at most N KB):` line in its first ten lines")
        continue
    budget, paths = found
    files = [PurePosixPath(p.replace("\\", "/").partition("#")[0]).as_posix() for p in paths]
    missing = [p for p, f in zip(paths, files) if not (ROOT / f).is_file() or bytes_of(p) < 0]
    if budget > START_KB or role not in files or missing or [f for f in files if f in SEARCHED]:
        reds.append(f"{role}: budget {budget} (max {START_KB}), names itself: {role in files}, missing {missing}, searched docs listed")
        continue
    sizes_of = {p: bytes_of(p) for p in paths}
    if any(s > MAX_LISTED_KB * 1024 for s in sizes_of.values()) or sum(sizes_of.values()) > budget * 1024:
        reds.append(f"{role}: start reads {sum(sizes_of.values()) / 1024:.1f} KB > {budget} KB ({sizes_of})")
print("size:", " ".join(sizes) + ("; " + "; ".join(reds) if reds else "; start reads ok"))
sys.exit(1 if reds else 0)
