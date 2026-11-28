"""The architect's review of the miss candidates, with no model: `kmap candidates` lists them by pair
and score; `--review` prints the top REVIEW_MAX waiting pairs as an accept/reject table, each by a stable pair id
(git-style: the shortest prefix of 6 hex or more of the hash of its two ends that no other pair shares), with each
pair's evidence (a pair back from a rejection quotes it and says why it is back). `--accept <id>,<id> --reject <id>
--reason ".."` writes those pairs into `docs/kmap/reviewed.md` (the architect commits it) with the date, the reviewer
and both ends' fingerprints; an id that names no pair, several, or one no longer waiting refuses the whole call. An
accepted pair is a curated `accepted` link with its provenance from the next build; a rejection is a snapshot
(`kmap_found.returned`). Every run writes `<cache>/review.json` for the coordinator's heartbeat."""
import datetime
import hashlib
import json
import os
import re
import time

from kmap_found import epoch, fp_of, iso, pairs, read
from kmap_parse import FOUND_MIN, ID_MIN, REVIEW_DAYS, REVIEW_MAX, TICK

REVIEWED = "docs/kmap/reviewed.md"
HEAD = ("# Reviewed miss candidates\n\nWritten by `tools/kmap/kmap.py candidates --accept/--reject` in the architect's "
        "pass; `kmap.py build` reads both tables. An accepted pair is a curated link of the map (type "
        "`accepted`, its evidence kept). A rejection is a snapshot, not a ban: the pair is back in review when an end's "
        "fingerprint changes, new evidence comes (2 mechanical, or 1 and a self-report), or after 20 merges that "
        "changed either end (30 days at most).\n")
TABLE = "| a | b | date | reviewer | reason | fingerprints | evidence |\n|---|---|---|---|---|---|---|\n"


def parse(text):
    """{"accepted": [row], "rejected": [row]}, a row {a, b, date, reviewer, reason, fps, evidence, line}."""
    out, part = {"accepted": [], "rejected": []}, None
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            part = line[3:].strip().lower() if line[3:].strip().lower() in out else None
        elif part and line.startswith("| `"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")] + [""] * 7
            ab = [t for c in cells[:2] for t in TICK.findall(c)]
            if len(ab) == 2:
                fps = (cells[5].split() + ["-", "-"])[:2]
                out[part].append({"a": ab[0], "b": ab[1], "date": cells[2], "reviewer": cells[3], "reason": cells[4],
                                  "fps": fps, "evidence": cells[6], "line": number})
    return out


def order(got):
    """The waiting pairs, best first: more mechanical evidence, then a self-report, then more kinds, then newer."""
    return sorted(((k, p) for k, p in got.items() if p["status"] == "waiting"),
                  key=lambda kv: (-len(kv[1]["mech"]), -len(kv[1]["weak"]), -len(kv[1]["kinds"]), -kv[1]["last"], kv[0]))


def evidence(p, cut=4):
    ids = sorted(p["mech"]) + sorted(p["weak"])
    kinds = " ".join(f"{k}{'' if n == 1 else f' x{n}'}" for k, n in sorted(p["kinds"].items()))
    return f"{kinds}: " + " ".join(ids[:cut]) + (f" +{len(ids) - cut}" if len(ids) > cut else "")


def before(p):
    """A pair back from a rejection: the old verdict quoted, and what changed."""
    w = p["was"]
    return f"rejected {w['date'][:10]} by {w['reviewer']}: \"{w['reason']}\"; {p['back']}" if w else "-"


def write_due(m, folder, got):
    """`review.json`: how many pairs wait, and since when (the last review, else the oldest waiting evidence)."""
    waiting = [p for p in got.values() if p["status"] == "waiting"]
    rows = json.loads(m.meta.get("reviewed") or "{}")
    last = max([epoch(r["date"]) for part in rows.values() for r in part] or [0.0])
    since = last or min([p["last"] for p in waiting] or [0.0])
    folder.mkdir(parents=True, exist_ok=True)
    cands = folder / "candidates.jsonl"           # its size: coord-beat marks the count `+` once lines come after it
    (folder / "review.json").write_text(json.dumps({"unreviewed": len(waiting), "since": int(since), "max": REVIEW_MAX,
                                                    "days": REVIEW_DAYS, "written": int(time.time()),
                                                    "size": cands.stat().st_size if cands.is_file() else 0}),
                                        encoding="utf-8")


def pair_hash(pair):
    """The hash a pair's id is a prefix of: from its two ends alone, so it stays the same however the list ranks it."""
    return hashlib.sha1("\n".join(pair).encode()).hexdigest()


def ids_of(keys):
    """{pair: its id}: the shortest prefix, ID_MIN hex or more, that no other pair's hash shares (git's short shas)."""
    hashed = sorted((pair_hash(k), k) for k in keys)
    out = {}
    for i, (h, k) in enumerate(hashed):
        near = [hashed[j][0] for j in (i - 1, i + 1) if 0 <= j < len(hashed)]
        common = max((len(os.path.commonprefix([h, o])) for o in near), default=0)
        out[k] = h[:max(ID_MIN, common + 1)]
    return out


def cell(text):
    return " ".join(str(text).split()).replace("|", "/")


def record(root, m, chosen, verdict, reviewer, reason):
    """Rows for `chosen` [(pair, p)] under `## Accepted` or `## Rejected` of the doc, which is made when missing."""
    path = root / REVIEWED
    text = path.read_text(encoding="utf-8") if path.is_file() else HEAD + "\n## Accepted\n\n" + TABLE + "\n## Rejected\n\n" + TABLE
    lines = text.splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.strip().lower() == f"## {verdict}")
    end = at + 1
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    while end > at + 1 and not lines[end - 1].strip():
        end -= 1
    now = datetime.datetime.now().isoformat(timespec="minutes")
    lines[end:end] = [f"| `{a}` | `{b}` | {now} | {cell(reviewer)} | {cell(reason) or '-'} | {fp_of(m, a)} {fp_of(m, b)} "
                      f"| {cell(evidence(p, 8))} |" for (a, b), p in chosen]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def picks(spec, got):
    """The pairs the ids `--review` printed name, or (None, why): an id must be ID_MIN hex or more and the prefix of
    exactly one pair's hash among all pairs, and that pair must be waiting; else nothing is taken."""
    out, hashes = [], {k: pair_hash(k) for k in got}
    for i in (x for x in re.split(r"[,\s]+", spec.strip().lower()) if x):
        hits = [k for k, h in hashes.items() if h.startswith(i)] if len(i) >= ID_MIN else []
        if len(hits) != 1:
            why = (f"shorter than {ID_MIN} hex" if len(i) < ID_MIN else "no such pair" if not hits else
                   f"{len(hits)} pairs start so (" + ", ".join(sorted(ids_of(got)[k] for k in hits)[:4]) + ")")
            return None, f"id {i}: {why}; run --review again"
        p = got[hits[0]]
        if p["status"] != "waiting":
            return None, f"id {i} ({hits[0][0]} ~ {hits[0][1]}) is {p['status']} now, not waiting: run --review again"
        out.append((hits[0], p))
    return out, ""

def cmd_candidates(root, m, a, path, folder, reopen=None):
    got = pairs(m, read(path))
    ranked, ids = order(got)[:a.top], ids_of(got)
    if a.accept or a.reject:
        chosen = {verdict: picks(spec, got) if spec else ([], "") for spec, verdict in
                  ((a.accept, "accepted"), (a.reject, "rejected"))}
        bad = [why for pick, why in chosen.values() if pick is None]
        if bad or {k for k, _ in chosen["accepted"][0]} & {k for k, _ in chosen["rejected"][0]}:
            print("kmap: nothing written: " + ("; ".join(bad) or "an id is both accepted and rejected"))
            return 2
        for verdict, (pick, _) in chosen.items():
            if pick:
                record(root, m, pick, verdict, a.reviewer, a.reason)
            for (x, y), _ in pick:
                print(f"{verdict}: {ids[(x, y)]} {x} ~ {y}")
        if reopen:                                   # the count the heartbeat shows: with this review in it
            now = reopen()
            write_due(now, folder, pairs(now, read(path)))
        print(f"written to {REVIEWED}: commit it; the next build takes it")
        return 0
    write_due(m, folder, got)
    by = {s: sum(p["status"] == s for p in got.values()) for s in
          ("found", "waiting", "collecting", "expired", "accepted", "rejected", "linked", "gone")}
    print(f"candidates: {len(got)} pairs (" + ", ".join(f"{k} {n}" for k, n in by.items() if n) + f"); a link from "
          f"{FOUND_MIN} mechanical evidences, or a self-report and one mechanical (`{path}`)")
    if a.review:
        print("| id | a | b | evidence | the agent's words | before |\n|---|---|---|---|---|---|")
        for (x, y), p in ranked:
            print(f"| {ids[(x, y)]} | `{x}` | `{y}` | {evidence(p)} | {cell('; '.join(p['why'])[:80]) or '-'} "
                  f"| {cell(before(p))} |")
        print(f"{len(ranked)} of {by['waiting']} waiting; accept or reject by id: kmap.py candidates --accept <id>,<id> "
              f"--reject <id> --reason \"..\" (writes {REVIEWED})")
        return 0
    for status in ("found", "waiting", "expired"):
        rows = sorted(((k, p) for k, p in got.items() if p["status"] == status),
                      key=lambda kv: (-len(kv[1]["mech"]), -len(kv[1]["weak"]), -kv[1]["last"], kv[0]))[:a.top]
        if rows:
            print(f"{status}:")
        for (x, y), p in rows:
            print(f" {len(p['mech'])}+{len(p['weak'])} {x} ~ {y} ({evidence(p)}; last "
                  f"{iso(p['last'])[:10] if p['last'] else '-'})" + (f" back: {before(p)}" if p["was"] else ""))
    return 0
