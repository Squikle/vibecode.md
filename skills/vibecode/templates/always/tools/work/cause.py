"""A merge's cause (`merge-cause`): the refs a `--from` text names, and the default when none is given.

A ref is a backticked batch id (`autodeploy`), a backticked docs path (`docs/specs/done/x.md`, `docs/QUESTIONS.md`,
`docs/DECISIONS.md#274`), or the owner's word with its day (`Owner 2026-11-24: "<quote>"`; any capitalized name
works in place of Owner). The default (`default_cause`): the batch's spec, done or open, then the refs on the `From:`
line of its lane's report. Used by merge-log.py (which refuses a text with no ref), `gate.py ship` (the default when
`--from` is not given) and integrate.py (refuses early), so a batch nobody can trace back never merges.
"""
import re
from pathlib import Path

WORD = r'[A-Z][\w-]* \d{4}-\d\d-\d\d: ["“][^"”]*["”]'
REF = re.compile(r'`(docs/[\w./-]+?\.md(?:#\d+)?)`|`([A-Za-z0-9][A-Za-z0-9-]*)`|(' + WORD + ')')
FROM_LINE = re.compile(r"^\s*(?:- )?(?:\*\*)?From:(?:\*\*)?\s*(.+)$", re.M)
# A report's `From:` line that names a cause (integrate.py's early check).
HAS_CAUSE = re.compile(r'^\s*(- )?(\*\*)?From:(\*\*)?.*(`[^`]+`|[A-Z][\w-]* \d{4}-\d\d-\d\d: ["“])', re.M)


def refs_of(text: str) -> list[str]:
    """The refs in `text`, in order, each once: as written (backticks and quotes kept)."""
    out = []
    for m in REF.finditer(text or ""):
        ref = f"`{m.group(1)}`" if m.group(1) else f"`{m.group(2)}`" if m.group(2) else m.group(3)
        if ref not in out:
            out.append(ref)
    return out


def default_cause(batch: str, exists, report: str) -> list[str]:
    """The spec (`exists(rel)` says if the tree has it), then the report's `From:` line's refs, each once."""
    spec = next((f"`{rel}`" for rel in (f"docs/specs/done/{batch}.md", f"docs/specs/{batch}.md") if exists(rel)), None)
    line = FROM_LINE.search(report or "")
    refs = ([spec] if spec else []) + (refs_of(line.group(1)) if line else [])
    return list(dict.fromkeys(refs))


def cause_in(batch: str, tree: Path, main: Path) -> str:
    """The default `--from` text of `batch` (one id, or ids apart by commas): its spec in `tree`, the `From:` line of
    `<main>/.work/<id>/report.md`; "" when there is none."""
    refs = []
    for i in [b for b in re.split(r"[,\s]+", batch) if b and b != "and"]:
        rep = Path(main) / ".work" / i / "report.md"
        text = rep.read_bytes().decode("utf-8", "replace") if rep.is_file() else ""
        refs += default_cause(i, lambda rel: (Path(tree) / rel).is_file(), text)
    return "; ".join(dict.fromkeys(refs))


if __name__ == "__main__":  # self-check: python -I tools/work/cause.py
    assert refs_of('`docs/specs/x.md` and `x-1`, Owner 2026-11-24: "do it"') == [
        "`docs/specs/x.md`", "`x-1`", 'Owner 2026-11-24: "do it"']
    assert refs_of("no ref here") == []
    assert HAS_CAUSE.search("- **From:** `parent-batch`") and not HAS_CAUSE.search("From: nothing")
    assert HAS_CAUSE.search('From: Owner 2026-11-24: "yes"')
    print("cause.py ok")
