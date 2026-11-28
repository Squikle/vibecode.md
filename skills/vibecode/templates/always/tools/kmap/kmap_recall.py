"""The recall meter: at a merge's first parent, the map built from that tree as it was (no checkout)
answers the batch's topic; its top K files are held against the code files the merge changed. One `RECALL` line;
with `--log` also a line in `<cache>/recall.jsonl` and the merge's misses and co-change pairs as candidates
(`kmap_found`). `--replay N`: the same for the last N merges of `docs/MERGES.md` that have a spec (the baseline)."""
import collections
import json
import posixpath
import re
import statistics

from kmap_found import append, co_change, compact, iso, pairs, read, write_found
from kmap_parse import CODE, RECALL_K, SKIPPED, run_git
from kmap_query import flow
from kmap_review import write_due
from kmap_store import CANDIDATES, RECALL_LOG, cache_dir, open_map, open_map_at

SPAN = 3          # a MERGES sha may be the merge, or a docs commit up to 2 first-parent steps past it
KS = (5, 10, 20)
MOVED = re.compile(r"^R\d*\tdocs/specs/([\w-]+)\.md\tdocs/specs/done/\1\.md$", re.M)


def is_code(path):
    return not path.startswith("docs/") and posixpath.splitext(path)[1] in CODE and not SKIPPED & set(path.split("/")[:-1])


def span(repo, sha):
    """(base, merge) for a MERGES sha: the first parent of the nearest merge commit at most SPAN first-parent steps
    down, else (sha^1, None) for a plain commit."""
    out = run_git(repo, "log", "--first-parent", f"-n{SPAN}", "--format=%H %P", sha) or ""
    for line in out.splitlines():
        own, *parents = line.split()
        if len(parents) > 1:
            return parents[0], own
    first = out.splitlines()[0].split() if out.strip() else []
    return (first[1] if len(first) > 1 else None), None


def moved_specs(lines):
    """The spec ids a `--name-status` listing moves from `docs/specs/` into `done/`."""
    return [m.group(1) for m in MOVED.finditer(lines)]


def changed(repo, base, sha):
    """(code files that existed at base, code files new in the merge, specs it moved to `done/`); a rename counts by
    its old path."""
    old, new = set(), set()
    listing = run_git(repo, "diff", "--name-status", "-M", base, sha) or ""
    for line in listing.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and is_code(parts[1]):
            (new if parts[0].startswith("A") else old).add(parts[1])
    return old, new, moved_specs(listing)


def topic_of(m, repo, batch, merge, moved):
    """The batch's spec: its id with a spec file, a spec the merge moved to `done/`, its id as a row, the branch."""
    ids = [f"spec:{batch}"] if m.nodes.get(f"spec:{batch}", {}).get("path") else []
    ids += [f"spec:{s}" for s in moved] + [f"spec:{batch}"]
    subject = (run_git(repo, "show", "-s", "--format=%s", merge) or "") if merge else ""
    got = re.match(r"Merge (?:branch )?'?([\w.-]+?)'?(?::|\s+into\b|\s*$)", subject.strip())
    ids += [f"spec:{got.group(1)}"] if got else []
    return next((t for t in ids if t in m.nodes), None)


def top_files(m, topic, k, role=None):
    ranked = flow(m, [topic], 2, role)[0]
    return [m.nodes[n]["path"] for n, _ in ranked if m.nodes[n]["kind"] in ("file", "test")][:k]


def measure(m, topic, old, new, k):
    top = top_files(m, topic, max(k, *KS))
    hits = {kk: len(set(top[:kk]) & old) for kk in KS + (k,)}
    total = len(old) + len(new)
    builder = len(set(top_files(m, topic, k, "builder")) & old)        # a lane asks with `--role builder`
    return {"k": k, "recall": round(hits[k] / total, 3) if total else None,
            "recall_builder": round(builder / total, 3) if total else None,
            "recall_old": round(hits[k] / len(old), 3) if old else None, "precision": round(hits[k] / k, 3),
            **{f"recall@{kk}": round(hits[kk] / total, 3) if total else None for kk in KS},
            "files": total, "new": len(new), "missed": sorted(old - set(top[:k]))}


def one(repo, sha, batch, cache=None, k=RECALL_K, found=True):
    """The measure of one merge, with what `--log` needs; None fields say why it was not measured."""
    base, merge = span(repo, sha)
    res = {"batch": batch, "sha": sha[:8], "base": (base or "")[:8], "merge": (merge or "")[:8]}
    if not base:
        return {**res, "why": "no parent"}, None, None
    old, new, moved = changed(repo, base, sha)
    m = open_map_at(repo, base, cache, found=found)
    topic = topic_of(m, repo, batch, merge, moved)
    res.update(topic=topic, spec_file=bool(topic and m.nodes[topic]["path"]))
    if not topic:
        return {**res, "why": "no spec or row at the parent"}, m, (old, new)
    if not old | new:
        return {**res, "why": "no code files"}, m, (old, new)
    return {**res, **measure(m, topic, old, new, k)}, m, (old, new)


def line(r):
    if "why" in r:
        return f"RECALL batch={r['batch']} sha={r['sha']} not measured: {r['why']}"
    missed = ",".join(r["missed"][:8]) + (f",+{len(r['missed']) - 8}" if len(r["missed"]) > 8 else "")
    return (f"RECALL batch={r['batch']} sha={r['sha']} k={r['k']} recall={r['recall']} precision={r['precision']} "
            f"new={r['new']} missed={missed or '-'}")


def merges_line(r):
    """The MERGES entry's `Recall:` bullet."""
    if "why" in r:
        return f"not measured ({r['why']})"
    hit = round(r["recall"] * r["files"])
    missed = ", ".join(f"`{p}`" for p in r["missed"][:3]) + (f" +{len(r['missed']) - 3}" if len(r["missed"]) > 3 else "")
    return (f"{r['recall']:.2f} ({hit} of {r['files']} code files in the map's top {r['k']} at `{r['base']}`; precision "
            f"{r['precision']:.2f}; {r['new']} new" + (f"; missed {missed}" if missed else "") + ")")


def candidates_of(r, m, files):
    """The recall misses (the spec and each of its modules against each missed file; a spec ~ file pair is linked by
    the merge itself at once, its modules' pairs can promote) and, for any merge with code files, its co-change pairs."""
    if not r.get("merge") or m is None:
        return []
    ct, topic = r["ct"], r.get("topic")
    mods = [o for o, t, label, *_ in m.adj.get(topic, []) if label == "part-of" and o.startswith("mod:")] if topic else []
    out = [{"a": a, "b": f, "kind": "recall-miss", "evidence": r["merge"], "at": iso(ct)}
           for f in r.get("missed", []) for a in [topic] + mods] if "why" not in r else []
    return out + co_change(m, files[0], r["merge"], ct)

def settle(root, cache, folder):
    """After a merge's candidates, against HEAD's map (what --review and queries see): stale collecting pairs leave
    the file, the found links a query reads are written once, and the heartbeat's count is HEAD's."""
    now = open_map(root, cache=cache)
    dropped = compact(now, folder / CANDIDATES)
    write_found(now, folder)
    write_due(now, folder, pairs(now, read(folder / CANDIDATES)))
    return dropped


def cmd_recall(root, a, cache):
    folder = cache_dir(root, cache)
    if a.replay:
        return replay(root, a, cache, folder)
    sha = (run_git(root, "rev-parse", "--verify", f"{a.sha}^{{commit}}") or "").strip()
    if not sha:
        print(f"RECALL batch={a.batch or '?'} sha={a.sha} not measured: no such commit")
        return 4
    batch = a.batch or a.sha
    r, m, files = one(root, sha, batch, cache, a.k)
    print(line(r))
    print("MERGES: " + merges_line(r))
    if a.log and m is not None:
        r["ct"] = int(run_git(root, "show", "-s", "--format=%ct", r.get("merge") or sha) or 0)
        with (folder / RECALL_LOG).open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({**r, "at": iso(r["ct"])}) + "\n")
        n = append(folder / CANDIDATES, candidates_of(r, m, files))
        dropped = settle(root, cache, folder)
        print(f"logged: {folder / RECALL_LOG}; {n} new candidates, {dropped} stale collecting lines dropped")
    return 0


def spec_merges(repo, rev="HEAD", n=1500):
    """[(sha, spec id)] of the first-parent commits (newest first) that moved a spec into `done/`: a batch whose spec
    was on main before its merge (a batch with no spec, as most fixes, has none to ask about)."""
    out, sha = [], None
    for ln in (run_git(repo, "log", "--first-parent", f"-n{n}", "--diff-merges=first-parent", "--name-status", "-M",
                       "--format=@@%H", rev) or "").splitlines():
        if ln.startswith("@@"):
            sha = ln[2:].strip()
        elif sha:
            out += [(sha, s) for s in moved_specs(ln)]
    return out


def window(repo, first, last):
    """[(ct, merge, first parent)] of every first-parent merge from `first` to `last` (commit times), oldest first."""
    out = []
    for ln in (run_git(repo, "log", "--first-parent", "--merges", "--format=%ct %H %P", "HEAD") or "").splitlines():
        ct, sha, base, *_ = ln.split()
        if first <= int(ct) <= last:
            out.append((int(ct), sha, base))
    return sorted(out)


def replay(root, a, cache, folder):
    """The true-replay baseline: the last N merges that moved a spec to `done/`, each at its first parent, with no
    found links (they would carry later evidence). With `--log`, as live: every first-parent merge between the first
    and the last of them, oldest first, adds its co-change pairs (and a measured one its recall misses), then the
    compaction the merge hook runs against that merge's map; HEAD's settle last."""
    rows, seen, made, later = [], set(), 0, []
    for sha, spec in spec_merges(root):
        if len(rows) >= a.replay:
            break
        base, merge = span(root, sha)
        if not merge or (merge, spec) in seen:
            continue
        seen.add((merge, spec))
        r, m, files = one(root, sha, spec, cache, a.k, found=False)
        print(line(r), flush=True)
        if "why" in r:
            continue
        r["ct"] = int(run_git(root, "show", "-s", "--format=%ct", merge) or 0)
        rows.append(r)
        if a.log:
            later.append((r["ct"], merge, candidates_of(r, m, files)))
    dropped = 0
    misses = collections.defaultdict(list)                    # a merge that built two specs has both their misses
    for ct, merge, lines in later:
        misses[merge] += [c for c in lines if c["kind"] == "recall-miss"]
    first, last = (min(x[0] for x in later), max(x[0] for x in later)) if later else (1, 0)
    for ct, merge, base in window(root, first, last):          # oldest first, as live
        old, _, _ = changed(root, base, merge)
        lines = misses.get(merge, []) + co_change(open_map_at(root, base, cache), old, merge, ct)
        made += append(folder / CANDIDATES, lines)
        dropped += compact(open_map_at(root, merge, cache), folder / CANDIDATES, ct + 1)
    if a.log:
        dropped += settle(root, cache, folder)
    (folder / "recall-baseline.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print(summary(rows) + (f"\n{made} new candidates in {folder / CANDIDATES}, {dropped} stale collecting lines "
                           "dropped along the way" if a.log else ""))
    return 0

def summary(rows):
    if not rows:
        return "baseline: no merge measured"
    out = [f"baseline over {len(rows)} merges with a spec, each at its first parent (mean / median):"]
    for key in [f"recall@{k}" for k in KS] + ["recall_old", "recall_builder", "precision"]:
        vals = [r[key] for r in rows if r.get(key) is not None]
        out.append(f"  {key:10} {statistics.mean(vals):.3f} / {statistics.median(vals):.3f}  (n={len(vals)})")
    out.append(f"  new files  {sum(r['new'] for r in rows) / max(1, sum(r['files'] for r in rows)):.3f} of "
               f"{sum(r['files'] for r in rows)} changed code files")
    return "\n".join(out)
