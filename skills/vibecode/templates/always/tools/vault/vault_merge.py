"""The vault's write step: every file vaultify builds (notes, the hub) reaches the vault, the owner's edits too.

`.vaultify/base/<rel>` holds the bytes this script last wrote at `rel` (a vault older than the base copies seeds it from a
file whose hash is still the manifest's). A file equal to its base is overwritten. An edited one gets the new docs by a
3-way `git merge-file <yours> <base> <new>` (LF for the merge, your CRLF put back): a clean merge is written (`merged`), a
conflict leaves your file alone and parks the new version at `.vaultify/conflicts/<rel>` (`conflicts`). An edited file
with no base, or one whose source vanished, is a conflict too: never kept silently. `vault-alert.json` next to the vault
lists the conflicts and the misses, and is deleted when both are empty; `resolve` takes a hand-merged file back."""
import hashlib, json, re, subprocess, tempfile, time
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)     # a scheduled run opens no console window on Windows
VOLATILE = re.compile(rb"(?m)^rev: .*\n|^Built \d{4}-\d\d-\d\d \d\d:\d\d")


def h12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def stable(data: bytes) -> bytes:
    """A file's bytes without its `rev:` line and the hub's build time: a new rev alone rewrites nothing."""
    return VOLATILE.sub(b"", data)


def save(p, data):
    if not p.is_file() or p.read_bytes() != data:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def merge3(mine, base, new):
    """`git merge-file -p`: the merged bytes, or None on a conflict or when git fails."""
    crlf = b"\r\n" in mine
    lf = lambda b: b.replace(b"\r\n", b"\n")
    with tempfile.TemporaryDirectory() as t:
        paths = []
        for name, data in (("yours", mine), ("base", base), ("new", new)):
            paths.append(str(Path(t) / name))
            Path(paths[-1]).write_bytes(lf(data))
        try:
            r = subprocess.run(["git", "merge-file", "-p", "-L", "yours", "-L", "base", "-L", "the new docs", *paths],
                               capture_output=True, creationflags=NO_WINDOW, timeout=30)
        except (OSError, subprocess.TimeoutExpired):   # in the git hook: a hang is a conflict, never a held commit
            return None
    if r.returncode:
        return None
    return r.stdout.replace(b"\n", b"\r\n") if crlf else r.stdout


class Sync:
    """One build's writes: `put` each file, then `finish` with every rel the build made."""

    def __init__(s, vault):
        s.vault, s.meta = vault, vault / ".vaultify"
        mp = s.meta / "manifest.json"
        s.old = json.loads(mp.read_bytes().decode("utf-8")) if mp.is_file() else {}
        s.new, s.conflicts = {}, []
        s.written = s.merged = s.deleted = 0

    def base(s, rel):
        return s.meta / "base" / rel

    def untouched(s, rel, cur):
        bp = s.base(rel)
        return stable(cur) == stable(bp.read_bytes()) if bp.is_file() else s.old.get(rel) == h12(cur)

    def write(s, rel, data):
        p = s.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        save(s.base(rel), data)
        s.new[rel], s.written = h12(data), s.written + 1

    def conflict(s, rel, data=None):
        if data is not None:
            save(s.meta / "conflicts" / rel, data)
        s.conflicts.append(rel)
        if rel in s.old:
            s.new[rel] = s.old[rel]

    def put(s, rel, data):
        p, bp = s.vault / rel, s.base(rel)
        if not p.is_file():
            return s.write(rel, data)
        cur = p.read_bytes()
        if not bp.is_file() and s.old.get(rel) == h12(cur):
            save(bp, cur)                                    # seeding: a file of an older build, unedited
        if stable(cur) == stable(data):
            s.new[rel] = h12(cur)
            if not bp.is_file():
                save(bp, cur)
            return None
        if not bp.is_file():
            return s.conflict(rel, data)                     # edited, or never ours, and nothing to merge against
        base = bp.read_bytes()
        if stable(base) == stable(cur):
            return s.write(rel, data)
        if stable(base) == stable(data):                     # the docs did not change since: your edit stands
            if rel in s.old:
                s.new[rel] = s.old[rel]
            return None
        merged = merge3(cur, base, data)
        if merged is None:
            return s.conflict(rel, data)
        if merged != cur:
            p.write_bytes(merged)
        save(bp, data)
        s.new[rel], s.merged = h12(merged), s.merged + 1
        return None

    def vanished(s, made):
        """The edited files whose source is gone (`finish` makes them conflicts), so the hub's warning counts them."""
        return [r for r in s.old if r not in made and (s.vault / r).is_file() and not s.untouched(r, (s.vault / r).read_bytes())]

    def finish(s, made):
        """Delete what vanished unedited (an edited one is a conflict), drop stale parked copies, save the manifest."""
        for rel, hsh in s.old.items():
            p = s.vault / rel
            if rel in made:
                continue
            if not p.is_file():
                s.drop(rel)
            elif s.untouched(rel, p.read_bytes()):
                p.unlink()
                s.drop(rel)
                s.deleted += 1
                prune(p, s.vault)
            else:
                s.conflict(rel)
        parked = s.meta / "conflicts"
        for q in sorted(parked.rglob("*"), reverse=True) if parked.is_dir() else ():
            if q.is_file() and q.relative_to(parked).as_posix() not in s.conflicts:
                q.unlink()
                prune(q, parked)
        save(s.meta / "manifest.json", (json.dumps(s.new, ensure_ascii=False, sort_keys=True, indent=0) + "\n").encode())

    def drop(s, rel):
        bp = s.base(rel)
        if bp.is_file():
            bp.unlink()
            prune(bp, s.meta / "base")


def prune(p, top):
    """Remove the empty folders above `p`, up to `top`."""
    for d in p.parents:
        if d == top or not d.is_dir() or any(d.iterdir()):
            break
        d.rmdir()


def alert_path(vault):
    return vault.parent / "vault-alert.json"


def alert(vault, rev, conflicts, misses):
    """Write `vault-alert.json` when anything failed to land, delete it when nothing did."""
    p = alert_path(vault)
    if conflicts or misses:
        body = {"rev": rev, "time": time.strftime("%Y-%m-%d %H:%M"), "vault": str(vault), "conflicts": sorted(conflicts),
                "misses": list(misses)}
        save(p, (json.dumps(body, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    elif p.is_file():
        p.unlink()


def callout(conflicts, misses, vault=None):
    """The hub's warning, at its top, while the alert exists ("" when it does not). The parked folder is named in full:
    Obsidian hides `.vaultify/`."""
    n, m = len(conflicts), len(misses)
    if not n and not m:
        return ""
    miss = (f"{m} miss{'es' if m != 1 else ''}: a doc's shape changed and its notes may be stale "
            "(`vault-alert.json` next to the vault)")
    if not n:
        return f"> [!warning] {miss}\n"
    names = " · ".join(f"[[{r[:-3] if r.endswith('.md') else r}]]" for r in sorted(conflicts))
    where = vault / ".vaultify" / "conflicts" if vault else ".vaultify/conflicts/"
    lines = [f"> [!warning] {n} note{'s' if n != 1 else ''} didn't get the new docs: {names}",
             f"> Each kept your edits; the new version is in `{where}` (hidden in Obsidian). Merge it in by hand, then "
             f"`python -I -X utf8 tools/vault/vaultify.py --vault {vault or '<vault>'} --resolve <path>`."]
    if m:
        lines.append(f"> Also {miss}.")
    return "\n".join(lines) + "\n"


def rel_of(vault, given):
    """A path as you may give it (vault-relative, absolute, or the parked copy's) -> its vault rel; None for a path
    outside the vault, inside `.vaultify/` (but its parked copy), or with `..`."""
    p = Path(given)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(vault.resolve())
        except ValueError:
            return None
    parts = Path(p.as_posix().replace("\\", "/")).as_posix().split("/")
    if parts[:2] == [".vaultify", "conflicts"]:
        parts = parts[2:]
    if not parts or parts[0] == ".vaultify" or any(x in ("", ".", "..") for x in parts):
        return None
    return "/".join(parts)


def resolve(vault, given):
    """After a hand merge: base := the parked new version, manifest := the file's hash; the parked copy goes and the
    alert is updated. A conflict with no parked copy (its source vanished) leaves the manifest: the file is yours now.
    The next build is not skipped (`build.json` dropped), so the hub's warning follows. (exit code, JSON): 2 and nothing
    changed for a path that is no conflict (not parked, not in the alert); your file itself is never touched."""
    meta, rel = vault / ".vaultify", rel_of(vault, given)
    ap = alert_path(vault)
    listed = json.loads(ap.read_bytes().decode("utf-8")).get("conflicts", []) if ap.is_file() else []
    if rel is None or not (meta / "conflicts" / rel).is_file() and rel not in listed:
        return 2, {"error": f"not a conflict of this vault: {given}", "conflicts": listed}
    mp, parked, p = meta / "manifest.json", meta / "conflicts" / rel, vault / rel
    man = json.loads(mp.read_bytes().decode("utf-8")) if mp.is_file() else {}
    if parked.is_file():
        save(meta / "base" / rel, parked.read_bytes())
        parked.unlink()
        prune(parked, meta / "conflicts")
        if p.is_file():
            man[rel] = h12(p.read_bytes())
    else:
        man.pop(rel, None)
        bp = meta / "base" / rel
        if bp.is_file():
            bp.unlink()
            prune(bp, meta / "base")
    save(mp, (json.dumps(man, ensure_ascii=False, sort_keys=True, indent=0) + "\n").encode())
    left = 0
    if ap.is_file():
        a = json.loads(ap.read_bytes().decode("utf-8"))
        a["conflicts"] = [c for c in a.get("conflicts", []) if c != rel]
        left = len(a["conflicts"])
        alert(vault, a.get("rev", "?"), a["conflicts"], a.get("misses", []))
    (meta / "build.json").unlink(missing_ok=True)
    return 0, {"resolved": rel, "conflicts": left}
