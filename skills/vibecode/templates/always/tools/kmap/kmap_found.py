"""Miss candidates: `<cache>/candidates.jsonl`, one JSON a line, `{"a": <node id>, "b": <path or node
id>, "kind": ..., "evidence": <merge sha or session id>, "at": <iso time of the evidence>}`. Mechanical kinds, found by
code: `recall-miss` and `co-change` (`kmap recall --log`, each merge) and `off-map-read` (role telemetry). Weak: a
`self-report` (`kmap miss`, with the agent's words in `why`). No model writes or types a link.

Promotion while the map is opened, never stored: a pair with FOUND_MIN independent mechanical evidences (merges or
sessions), or a self-report backed by at least one mechanical one, is a `found` link (half a link's weight, its
evidence kept); FOUND_EXPIRY merges touching a file end since its last evidence expire it. A self-report alone never
promotes. Each evidence is one detection: a co-change line names only the merge that changed both ends, so a link needs
FOUND_MIN distinct such merges; below that a co-change-only pair is `collecting` (it promotes by itself or never). A
spec ~ file recall miss never promotes: the merge's own implemented-in link holds that pair at once (`linked`); the
spec's modules ~ file misses can. The rest waits for the architect's review (`kmap_review`); a pair accepted there is
a curated link. A rejection is a snapshot of both ends' fingerprints, not a ban: the pair comes back (`returned`) when
an end changes, new independent evidence arrives, or the rejection expires."""
import collections
import datetime
import hashlib
import itertools
import json
import os
import re
import time

from kmap_parse import (CO_MAX, COLLECT_DAYS, COLLECT_MERGES, EDGE, FOUND_EXPIRY, FOUND_MIN, REJECT_DAYS,
                        REJECT_MERGES)

MECHANICAL = ("recall-miss", "co-change", "off-map-read")
WEAK = "self-report"
VOLATILE = re.compile(r"^(?:Status|Review):")


def iso(ct):
    return datetime.datetime.fromtimestamp(int(ct)).astimezone().isoformat(timespec="seconds")


def epoch(at):
    try:
        return datetime.datetime.fromisoformat(at).timestamp()
    except (TypeError, ValueError):
        return 0.0


def read(path, before=None, text=None):
    """The candidates, each line once (a replay run twice adds nothing), those after `before` (epoch) left out;
    `text` instead of the file's (what `compact` read)."""
    out, seen = [], set()
    if text is None:
        if not path or not path.is_file():
            return out
        text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        key = (c.get("a"), c.get("b"), c.get("kind"), c.get("evidence"))
        if not all(key) or c["kind"] not in MECHANICAL + (WEAK,) or key in seen \
                or (before is not None and epoch(c.get("at")) > before):
            continue
        seen.add(key)
        out.append(c)
    return out


def append(path, lines):
    have = {(c["a"], c["b"], c["kind"], c["evidence"]) for c in read(path)}
    new = [c for c in lines if (c["a"], c["b"], c["kind"], c["evidence"]) not in have]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.writelines(json.dumps(c, ensure_ascii=False) + "\n" for c in new)
    return len(new)


def node_of(m, token):
    token = (token or "").strip().replace("\\", "/")
    for cand in (token, f"file:{token}", m.alias.get(token), m.alias.get(f"file:{token}")):
        if cand and cand in m.nodes:
            return cand
    return None


def edge_types(m, a, b):
    return {t for o, t, *_ in m.adj.get(a, []) if o == b}


def times_of(m, end):
    """The merge times of a file end (`extra.merged`, its last FILE_MERGES); none for a spec or module end, whose
    change shows in its fingerprint instead."""
    n = m.nodes.get(end) if end and end.startswith("file:") else None
    return set(n["extra"].get("merged", [])) if n else set()


def touched(m, ends, after):
    """Merges after `after` that changed either end."""
    return len({t for e in ends for t in times_of(m, e) if t > after})


def one_sided(m, ends, after):
    """Merges after `after` that changed one end without the other (a merge changing both is a co-change)."""
    a, b = (times_of(m, e) for e in ends)
    return sum(t > after for t in a ^ b)


def expired(m, ends, last):
    """FOUND_EXPIRY merges touched an end since the pair's last evidence."""
    return touched(m, ends, last) >= FOUND_EXPIRY


def spec_fp(lines):
    """A spec's fingerprint: its text without the lines that move by themselves (`Status:`, `Review:`)."""
    return hashlib.sha1("\n".join(ln.rstrip() for ln in lines if not VOLATILE.match(ln)).encode()).hexdigest()[:12]


def fp_of(m, nid):
    """An end's fingerprint now: a file's blob sha, a spec's `spec_fp`, else its one-liner and status."""
    n = m.nodes.get(nid)
    if not n:
        return "-"
    return n["extra"].get("fp") or hashlib.sha1(f"{n['l0']}\n{n['status']}".encode()).hexdigest()[:12]


def rejections(m):
    """{(a, b): the latest rejection row} from `docs/kmap/reviewed.md` as the build read it."""
    out = {}
    for row in json.loads(m.meta.get("reviewed") or "{}").get("rejected", []):
        key = tuple(sorted(row["ends"]))
        if key not in out or row["date"] >= out[key]["date"]:
            out[key] = row
    return out


def returned(m, row, events, now):
    """Why a rejected pair is back in review, or "" (a rejection is a snapshot, not a ban): an end
    changed since, new independent evidence came after it (2 mechanical, or 1 and a self-report), or it expired
    (REJECT_MERGES merges that changed either end, REJECT_DAYS at most)."""
    then = epoch(row["date"])
    moved = [e for e, fp in zip(row["ends"], row["fps"]) if fp and fp != "-" and fp_of(m, e) != fp]
    if moved:
        return "changed since: " + ", ".join(f"`{e}`" for e in moved)
    mech = {ev for kind, ev, at in events if at > then and kind != WEAK}
    weak = {ev for kind, ev, at in events if at > then and kind == WEAK}
    if len(mech) >= 2 or (mech and weak):
        return f"new evidence since: {len(mech)} mechanical, {len(weak)} self-report"
    merges = touched(m, row["ends"], then)                 # that changed either end, never main's at large
    if now - then >= REJECT_DAYS * 86400 or merges >= REJECT_MERGES:
        return f"the rejection expired ({int((now - then) // 86400)} days, {merges} merges on its ends)"
    return ""


def pairs(m, cands, now=None):
    """{(a, b): {kinds, mech, weak, why, last, status, was, back}}; a pair's ends sorted, so one seen both ways is
    one. Status: found, waiting (for the review), expired, accepted, linked (a link holds it already), rejected (its
    snapshot holds), gone. A rejected pair that `returned` waits again with the rejection in `was`: never a found link
    before a reviewer sees it."""
    out, rej, now = {}, rejections(m), time.time() if now is None else now
    for c in cands:
        a, b = node_of(m, c["a"]) or c["a"], node_of(m, c["b"]) or c["b"]
        p = out.setdefault(tuple(sorted((a, b))), {"kinds": collections.Counter(), "mech": set(), "weak": set(),
                                                   "why": [], "last": 0.0, "events": [], "was": None, "back": ""})
        p["kinds"][c["kind"]] += 1
        p["weak" if c["kind"] == WEAK else "mech"].add(c["evidence"])
        p["why"] += [c["why"]] if c.get("why") and c["why"] not in p["why"] else []
        p["events"].append((c["kind"], c["evidence"], epoch(c.get("at"))))
        p["last"] = max(p["last"], epoch(c.get("at")))
    for (a, b), p in out.items():
        types = edge_types(m, a, b) - {"found"}
        up = len(p["mech"]) >= FOUND_MIN or (p["weak"] and p["mech"])
        alone = set(p["kinds"]) == {"co-change"}           # co-change alone below FOUND_MIN merges: it promotes by
        p["status"] = ("gone" if a not in m.nodes or b not in m.nodes else "accepted" if "accepted" in types else
                       "linked" if types else "rejected" if (a, b) in rej else
                       ("expired" if expired(m, (a, b), p["last"]) else "found") if up else
                       "collecting" if alone else "waiting")          # itself or never, so it waits for nobody
        if p["status"] == "rejected":
            p["was"], p["back"] = rej[(a, b)], returned(m, rej[(a, b)], p["events"], now)
            p["status"] = "waiting" if p["back"] else "rejected"
    return out


def found_edges(m, path, before=None):
    """The `found` links of the candidates file: (src, "found", dst, weight, "FOUND", evidence)."""
    out = []
    for (a, b), p in sorted(pairs(m, read(path, before)).items()):
        if p["status"] == "found":
            why = ",".join(f"{k} x{n}" for k, n in sorted(p["kinds"].items()))
            out.append((a, "found", b, EDGE["found"], "FOUND", f"{why}: " + " ".join(sorted(p["mech"] | p["weak"])[:6])))
    return out


def found_cached(m, folder):
    """The found links a query adds: `<folder>/found.json`, written by the merge hook (`write_found`); recounted from
    the candidates only when that file, the candidates (their size and time) or the map changed since."""
    path, cache = folder / "candidates.jsonl", folder / "found.json"
    st = path.stat() if path.is_file() else None
    key = f"{st.st_size}:{st.st_mtime_ns}:{m.meta.get('key', '')}" if st else "none"
    try:
        got = json.loads(cache.read_text(encoding="utf-8"))
        if got.get("key") == key:
            return [tuple(e) for e in got["edges"]]
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return write_found(m, folder, key)


def write_found(m, folder, key=None):
    path = folder / "candidates.jsonl"
    if key is None:
        st = path.stat() if path.is_file() else None
        key = f"{st.st_size}:{st.st_mtime_ns}:{m.meta.get('key', '')}" if st else "none"
    edges = found_edges(m, path) if path.is_file() else []
    try:
        tmp = folder / f"found.json.tmp-{id(edges)}"
        tmp.write_text(json.dumps({"key": key, "edges": edges}), encoding="utf-8")
        tmp.replace(folder / "found.json")
    except OSError:
        pass                                  # another process wrote it; this one's edges are as good
    return edges


def compact(m, path, now=None):
    """At merge time: a co-change pair still collecting leaves the file after COLLECT_MERGES merges that changed one of
    its ends without the other, or COLLECT_DAYS days with no new evidence (found, waiting, rejected, accepted and
    linked pairs stay). Other writers (`kmap miss`, role telemetry) append at any time: the kept lines go to a temp
    file, any tail appended meanwhile is carried over, then it replaces the log. Returns the lines dropped."""
    now = time.time() if now is None else now
    data = path.read_bytes() if path.is_file() else b""
    size = data.rfind(b"\n") + 1                              # a line half written goes with the tail, once whole
    cands = read(path, text=data[:size].decode("utf-8"))
    got = pairs(m, cands, now)
    stale = {k for k, p in got.items() if p["status"] == "collecting" and (
        now - p["last"] >= COLLECT_DAYS * 86400 or one_sided(m, k, p["last"]) >= COLLECT_MERGES)}
    if not stale:
        return 0
    keep = [c for c in cands if key_of(m, c) not in stale]
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_bytes("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in keep).encode("utf-8"))
    before_swap()
    for _ in range(20):
        tail = path.read_bytes()[size:]
        whole = tail[:tail.rfind(b"\n") + 1]
        if whole:                                             # whole lines appended since the read: they go along
            with tmp.open("ab") as f:
                f.write(whole)
            size += len(whole)
            continue
        if tail:                                              # a line half written: let its writer finish it
            time.sleep(0.05)
            continue
        try:
            os.replace(tmp, path)
            return len(cands) - len(keep)
        except PermissionError:                               # Windows: a writer holds it open; once more
            time.sleep(0.05)
    tmp.unlink(missing_ok=True)                               # never won the race: the log stays as it is
    return 0


def before_swap():
    """A test's seam: what happens between the temp file's write and the swap (another writer's append)."""

def key_of(m, c):
    return tuple(sorted((node_of(m, c["a"]) or c["a"], node_of(m, c["b"]) or c["b"])))


def co_change(m, files, sha, ct):
    """One co-change candidate per pair of this merge's files (at most CO_MAX code files) that no link holds, with this
    merge alone as its evidence (one detection is one evidence, never its history's merges): a pair
    becomes a link once FOUND_MIN distinct merges, each changing both ends, wrote it."""
    files = sorted(f for f in set(files) if f"file:{f}" in m.nodes)
    if not 2 <= len(files) <= CO_MAX:
        return []
    return [{"a": f"file:{x}", "b": y, "kind": "co-change", "evidence": sha[:8], "at": iso(ct)}
            for x, y in itertools.combinations(files, 2) if not edge_types(m, f"file:{x}", f"file:{y}")]


def self_report(m, a, path, now):
    """`kmap miss <topic> <found> [--why]`: the topic resolved to a node, what was found as a node or a path."""
    from kmap_query import refuse, resolve
    state, ids = resolve(m, a.topic)
    if state != "ok":
        return refuse(state, a.topic, ids)
    found = node_of(m, a.found) or a.found.strip().replace("\\", "/")
    line = {"a": ids[0], "b": found, "kind": WEAK, "evidence": a.session or "no-session",
            "why": " ".join((a.why or "").split())[:300], "at": iso(now)}
    append(path, [line])
    from kmap_review import write_due
    write_due(m, path.parent, pairs(m, read(path)))
    print(f"logged: self-report {ids[0]} ~ {found} ({path}); weak evidence: a link only with a mechanical source "
          "(a recall miss, a co-change, an off-map read) or the architect's yes")
    return 0
