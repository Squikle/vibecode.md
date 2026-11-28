"""Inventory mode: `kmap.py list <area|module|folder> [--kind file|spec|test|symbol] [--group] [--level 0|1]` lists
every member, unranked and never cut: an area's mapped files, a module's members, a folder's files; `--group` by
sub-folder (files) or module (specs); one-liners at level 1, one name a line past LIST_COMPACT items. `hint` is the
line a `kmap.py <topic>` answer starts with when its topic is an area, a module or a folder and nothing relates to it
above LIST_FLOOR: that question was "what is in X", not "what relates to X"."""
import collections
import posixpath

from kmap_lines import symbols
from kmap_parse import LIST_COMPACT, LIST_FLOOR
from kmap_query import resolve, refuse, short

KINDS = ("file", "test", "spec", "symbol")


def folder_of(m, topic):
    """The folder a topic names (`src/app/view`, `view/`, `view`), when exactly one holds code files."""
    t = topic.strip().replace("\\", "/").strip("/")
    roots = set()
    for n in m.nodes.values():
        p = n["path"] or ""
        if n["kind"] in ("file", "test") and (p.startswith(t + "/") or f"/{t}/" in p):
            roots.add(p[:p.index(t + "/") + len(t)] if p.startswith(t + "/") else p[:p.index(f"/{t}/") + len(t) + 1])
    return roots.pop() if len(roots) == 1 else None


def members(m, nid=None, folder=None):
    if folder:
        return sorted(k for k, n in m.nodes.items() if n["kind"] in ("file", "test") and
                      (n["path"] or "").startswith(folder + "/"))
    label = {"mod": "has", "area": "maps"}[nid.split(":", 1)[0]]
    return sorted({o for o, t, lab, *_ in m.adj.get(nid, []) if lab == label})


def target(m, topic):
    """(node id or None, folder or None) of a list topic, or (None, None)."""
    state, ids = resolve(m, topic)
    if state == "ok" and ids[0].split(":", 1)[0] in ("area", "mod"):
        return ids[0], None
    folder = folder_of(m, topic)
    return (None, folder) if folder else (None, None)


def hint(m, seed, ranked):
    """The inventory hint for an area or a module whose best relation is under LIST_FLOOR, else ""."""
    if seed.split(":", 1)[0] in ("area", "mod") and (not ranked or ranked[0][1] < LIST_FLOOR):
        return (f"hint: this looks like an inventory question: kmap.py list {seed.split(':', 1)[1]} --group "
                f"(nothing relates above {LIST_FLOOR})")
    return ""


def group_of(m, nid, base):
    n = m.nodes[nid]
    if n["kind"] == "spec":
        mods = sorted(o[4:] for o, t, label, *_ in m.adj.get(nid, []) if label == "part-of" and o.startswith("mod:"))
        return f"module {mods[0]}" if mods else "(no module)"
    folder = posixpath.dirname(n["path"] or "")
    rel = folder[len(base):].lstrip("/") if base and folder.startswith(base) else folder
    return (rel or base) + "/"                                  # the list's own folder by its full path


def rows(m, ids, kind, root):
    """[(member id, name, one-liner)]; `symbol`: each top-level symbol and method of the member files."""
    if kind != "symbol":
        out = []
        for i in ids:
            n = m.nodes[i]
            if not kind or n["kind"] == kind:
                spec = n["kind"] == "spec"
                out.append((i, short(m, i) if spec or not n["path"] else n["path"],
                            n["l0"] + (f" ({n['status'][:30]})" if spec and n["status"] else "")))
        return out
    out = []
    for i in ids:
        p = m.nodes[i]["path"]
        if m.nodes[i]["kind"] == "file" and p and root:
            try:
                text = (root / p).read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            out += [(i, f"{p}:{a}-{b} {name}", "") for name, a, b in symbols(p, text)]
    return out


def cmd_list(m, a, root):
    nid, folder = target(m, a.topic)
    if not nid and not folder:
        state, ids = resolve(m, a.topic)
        if state == "ok":
            print(f"list takes an area, a module or a folder; {a.topic} is {short(m, ids[0])}: kmap.py {a.topic}")
            return 2
        return refuse(state, a.topic, ids)
    got = rows(m, members(m, nid, folder), a.kind, root)
    paths = [m.nodes[i]["path"] for i, *_ in got if m.nodes[i]["kind"] in ("file", "test")]
    base = folder or (posixpath.commonpath(paths) if paths and all("/" in p for p in paths) else "")
    base = base if not any(p == base for p in paths) else posixpath.dirname(base)
    count = collections.Counter(m.nodes[i]["kind"] for i, *_ in got)
    compact = a.level == 0 or len(got) > LIST_COMPACT
    print(f"list {short(m, nid) if nid else 'folder ' + folder}: {len(got)} " + ("symbols" if a.kind == "symbol" else
          "members: " + ", ".join(f"{k} {n}" for k, n in sorted(count.items()))) + (f", under {base}/" if base else "")
          + ("; one name a line (--level 0, or over %d)" % LIST_COMPACT if compact else ""))
    groups = collections.defaultdict(list)
    for i, name, l0 in got:
        groups[group_of(m, i, base) if a.group else ""].append((name, l0))
    for g in sorted(groups):
        if g:
            print(f"{g} ({len(groups[g])})")
        for name, l0 in groups[g] if a.kind == "symbol" else sorted(groups[g]):      # symbols in file order
            shown = posixpath.basename(name) if g and "/" in name else name     # its folder is the group's line
            print(("  " if g else "") + shown + ("" if compact or not l0 else f" \u00b7 {l0[:100]}"))
    if not got:
        print(" no member" + (f" of kind {a.kind}" if a.kind else ""))
    return 0
