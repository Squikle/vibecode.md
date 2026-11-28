"""Per-session numbers for `tools/work/role-telemetry.py`: each session's $ and tokens, its first request (the
fixed cost of its setup), its lookups against its knowledge-map calls, whole reads of big files, repeated errors, and
the files it read off the map twice or more (appended to `.work/kmap/candidates.jsonl` as `off-map-read` links, one
JSON per line, for the map's upkeep). Every row is plain JSON (`rows()`), so a later digest
reads the same records from `<date>.json` instead of scanning the database again."""
import collections
import json
import pathlib
import re
import time

KMAP = re.compile(r"tools[\\/]kmap[\\/]kmap\.py", re.I)
SEARCH = re.compile(r"\b(git\s+grep|grep|rg|select-string|sls|findstr)\b", re.I)
GETCONTENT = re.compile(r"\b(get-content|gc|cat|type)\s+[^|;\s]", re.I)
SEGMENT = re.compile(r"[;\n]|\|(?!\|)|&&")
BIG_KB = 50          # a whole read of a file this big is a flag: read it by section or ask kmap
LOOKUPS_MIN = 15     # raw lookups (greps, Select-String, Get-Content) before the ratio counts
LOOKUPS_RATIO = 2    # ... and at least this many per kmap call


def new_rec():
    return {"calls": 0, "replaceable": 0, "kmap": 0, "search": 0, "getcontent": 0, "reads": 0, "big": [],
            "errors": 0, "errs": collections.Counter()}


def note_call(rec, tool, inp, status, kind, error=""):
    """Counts one finished tool call into a session's record."""
    rec["calls"] += 1
    rec["replaceable"] += kind == "REPLACEABLE"
    if tool in ("shell", "bash"):
        cmd = inp.get("command") or ""
        if KMAP.search(cmd):
            rec["kmap"] += 1
        else:
            segs = [s for s in SEGMENT.split(cmd) if s.strip()]
            rec["search"] += any(SEARCH.search(s) for s in segs)
            rec["getcontent"] += any(GETCONTENT.search(s) for s in segs)
    elif tool == "grep":
        rec["search"] += 1
    elif tool == "read":
        rec["reads"] += 1
        path = str(inp.get("path") or inp.get("filePath") or "")
        if not (inp.get("offset") or inp.get("limit")):
            try:
                kb = pathlib.Path(path).stat().st_size // 1024
            except (OSError, ValueError):
                kb = 0
            if kb >= BIG_KB:
                rec["big"].append([path.replace("\\", "/"), kb])
    if status == "error":
        rec["errors"] += 1
        what = re.sub(r"\d+", "N", (inp.get("command") or tool)[:80]) + " -> " + re.sub(r"\d+", "N", str(error)[:60])
        rec["errs"][what] += 1


def rows(per, meta, roles, first, offmap):
    """One plain record per session in the window, most expensive first."""
    out = []
    for sid in dict.fromkeys([*meta, *per]):  # every session in the window, a call or not: spend shows in full
        rec, m = per.get(sid) or new_rec(), meta.get(sid, {})
        rep = [w for w, n in rec["errs"].items() if n >= 2]
        out.append({"id": sid, "role": roles.get(sid, "other"), "project": m.get("project", "this"),
                    "title": (m.get("title") or "")[:60],
                    "parent": m.get("parent"), "created": m.get("created"), "updated": m.get("updated"),
                    "model": m.get("model", ""), "usd": m.get("usd", 0.0), "kind": m.get("kind", "unknown"),
                    "api_usd": m.get("api_usd", 0.0), "list_usd": m.get("list_usd", 0.0),
                    "tokens": m.get("tokens", 0), "first": first.get(sid, 0), "calls": rec["calls"],
                    "replaceable": rec["replaceable"], "kmap": rec["kmap"], "search": rec["search"],
                    "getcontent": rec["getcontent"], "reads": rec["reads"], "offmap": offmap.get(sid, 0),
                    "big_reads": rec["big"], "errors": rec["errors"], "repeat_errors": rep})
    return sorted(out, key=lambda r: -r["usd"])


def flags(rs):
    """(key, text, role, rule, n, share) per session pattern: lookups over the map, a big whole read, an error loop."""
    out = []
    for r in rs:
        name = r["title"].split(":")[0] or r["id"]
        raw = r["search"] + r["getcontent"]
        if raw >= LOOKUPS_MIN and raw >= LOOKUPS_RATIO * r["kmap"]:
            out.append((f"lookups:{r['id']}", f"{name} ({r['role']}): {raw} raw lookups ({r['search']} greps, "
                        f"{r['getcontent']} Get-Content) against {r['kmap']} kmap calls: ask the map first",
                        r["role"], "lookups", raw, 0))
        for path, kb in r["big_reads"]:
            out.append((f"bigread:{r['id']}:{path}", f"{name} ({r['role']}): read {path.rsplit('/', 1)[-1]} whole "
                        f"({kb} KB): by section, or kmap.py <topic> --level 3", r["role"], "big-read", kb, 0))
        for what in r["repeat_errors"][:2]:
            out.append((f"errloop:{r['id']}:{what[:40]}", f"{name} ({r['role']}): the same error twice or more: "
                        f"`{what}`", r["role"], "error-loop", 2, 0))
    return out


def rel(path, root):
    """A read's path relative to the repository: from the first part that exists in `root` (else as read)."""
    parts = path.replace("\\", "/").split("/")
    for i in range(len(parts)):
        if parts[i] and ":" not in parts[i] and (root / parts[i]).exists() and (root / "/".join(parts[i:])).exists():
            return "/".join(parts[i:])
    for i in range(len(parts)):
        if parts[i] in ("docs", "web", "api", "tools"):
            return "/".join(parts[i:])
    return path


def candidates(off_paths, seeds, root, now=None):
    """`off-map-read` links: a file read off the map 2+ times in one session, against the topic it asked."""
    at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now or time.time()))
    out = []
    for sid, counts in off_paths.items():
        if not seeds.get(sid):
            continue
        for p, n in sorted(counts.items()):
            if n >= 2:
                out.append({"a": seeds[sid], "b": rel(p, root), "kind": "off-map-read", "evidence": sid, "at": at})
    return out


def append_candidates(path, cands):
    """Appends the new ones (same a, b, kind and evidence = already there); returns how many were written."""
    path = pathlib.Path(path)
    have = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                c = json.loads(line)
                have.add((c.get("a"), c.get("b"), c.get("kind"), c.get("evidence")))
            except ValueError:
                continue
    new = [c for c in cands if (c["a"], c["b"], c["kind"], c["evidence"]) not in have]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.writelines(json.dumps(c) + "\n" for c in new)
    return len(new)


def project_table(spend, js):
    """All spend this week in one place, per project (role_data.project_spend); budgets count `this` only."""
    js["projects"] = spend
    lines = ["## Spend per project this week", "", "| Project | Runs | API $ | Subscription at list $ |", "|---|---:|---:|---:|"]
    lines += [f"| {p} | {o['runs']} | {o['api']:.2f} | {o['list']:.2f} |" for p, o in spend.items()]
    return "\n".join(lines + ["", "Role budgets and `roles.py may` count `this` project only (roles.json `projects`)."])


def write_outputs(out_dir, cand_path, md, js, off_paths, seeds, root):
    """`<out_dir>/<date>.md` + `<date>.json`, and the new off-map candidates appended to `cand_path`."""
    if cand_path:
        js["candidates_new"] = append_candidates(cand_path, candidates(off_paths, seeds, root))
    if out_dir:
        out = pathlib.Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y-%m-%d")
        (out / f"{day}.md").write_text(md + "\n", encoding="utf-8", newline="\n")
        (out / f"{day}.json").write_text(json.dumps(js, indent=1, default=str), encoding="utf-8", newline="\n")


def table(rs, keep=(), top=25):
    """The per-session markdown table: the `top` most expensive plus every session in `keep`."""
    pick = [r for i, r in enumerate(rs) if i < top or r["id"] in keep]
    lines = ["| Session | Project | Role | Title | Model | API $ | List $ | Tokens | 1st request | Calls | kmap : lookups "
             "| Off-map | Errors |", "|---|---|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|"]
    for r in pick:
        lines.append(f"| `{r['id']}` | {r['project']} | {r['role']} | {r['title'][:40]} | {r['model']} | {r['api_usd']:.2f} | "
                     f"{r['list_usd']:.2f} | {r['tokens']:,} | {r['first']:,} | {r['calls']} | {r['kmap']} : "
                     f"{r['search'] + r['getcontent']} | {r['offmap']} | {r['errors']} |")
    return "\n".join(lines + ["", "API $ = paid; List $ = a subscription session's tokens at list prices (no cost "
                              "recorded). Every project; budgets count `this` only."])
