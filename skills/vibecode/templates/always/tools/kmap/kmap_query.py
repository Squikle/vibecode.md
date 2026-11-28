"""The knowledge map's answers: a topic resolved (never a guess between two), a flow from it ranked and cut to a
budget, the detail each level adds, the `(seen)` marks and the LOOP line of a session, and `path a b`."""
import json
import math
import re

from kmap_lines import ranges, terms
from kmap_parse import ALPHA, BUDGET, HUB_MIN, HUBBY, KINDS, MARK, ROLES, THETA, WEAK


def resolve(m, topic):
    """("ok", [id]) | ("ambiguous", candidates) | ("none", near names)."""
    t = topic.strip().replace("\\", "/")
    kind, _, key = t.partition(":")
    if kind in KINDS and key:
        for cand in (t, m.alias.get(t)):
            if cand in m.nodes:
                return "ok", [cand]
        return "none", near(m, key)
    exact = [f"{k}:{t}" for k in ("mod", "spec", "dec", "area") if f"{k}:{t}" in m.nodes]
    files = [n for n in m.nodes if n.startswith("file:") and (n[5:] == t or n[5:].endswith("/" + t))]
    if not files and f"file:{t}" in m.alias:
        files = [m.alias[f"file:{t}"]]
    exact += files if len(files) == 1 or not exact else []
    if len(exact) == 1:
        return "ok", exact
    if exact:
        return "ambiguous", exact
    cands = near(m, t)
    return ("ok", cands) if len(cands) == 1 else ("ambiguous" if cands else "none", cands)


def near(m, words, top=10):
    ws = [w for w in re.split(r"[\s/:._-]+", words.lower()) if w]
    scored = []
    for nid, n in m.nodes.items():
        if n["kind"] == "q":
            continue   # a question row is found through what it asks about, never as a name
        idl, text = nid.lower(), f"{nid} {n['l0']}".lower()
        if ws and all(w in text for w in ws):
            scored.append((-sum(w in idl for w in ws), len(nid), nid))
    return [nid for *_, nid in sorted(scored)[:top]]


def refuse(state, topic, cands):
    if state == "ambiguous":
        print(f"ambiguous: {topic} could be " + " | ".join(cands[:10]) + "; ask again with one of these ids")
        return 3
    print(f"not found: {topic}" + (f"; near: {' | '.join(cands[:10])}" if cands else "; nothing near it"))
    return 4


def blocked_by(m, seeds):
    out = set()
    for s in seeds:
        for target in m.nodes[s]["extra"].get("not", []):
            out.add(target)
            if target.startswith("mod:"):
                out |= {o for o, t, label, *_ in m.adj[target] if label == "has"}
    return out - set(seeds)


def damp(m, node):
    d = m.deg.get(node, 1)
    return 1 / (1 + math.log(d)) if node.split(":", 1)[0] in HUBBY and d > 1 else 1.0


def flow(m, seeds, depth, role=None, types=None):
    """A PathRAG-style flow from the seeds -> (ranked [(id, score)], via {id: (from, label, conf)}, mass lost)."""
    prof = ROLES.get(role or "", {})
    block = blocked_by(m, seeds)
    score, via, best = {s: 1.0 for s in seeds}, {}, {}
    frontier, reached, lost = list(seeds), set(seeds), 0.0
    for _ in range(depth):
        nxt = []
        for n in frontier:
            nb = m.adj.get(n, [])
            split = score[n] / math.sqrt(len(nb)) if nb else 0.0
            if split < THETA:
                lost += 0.0 if n in seeds else score[n]
                continue
            for o, t, label, w, conf in nb:
                if o in block or o in seeds or (types and t not in types):
                    continue
                g = ALPHA * split * w * prof.get(t, 1.0) * damp(m, o)
                if g < THETA:
                    lost += g
                    continue
                score[o] = score.get(o, 0.0) + g
                if g > best.get(o, 0.0):
                    best[o], via[o] = g, (n, label, conf)
                if o not in reached:
                    reached.add(o)
                    nxt.append(o)
        frontier = nxt
    ranked = sorted(((n, s) for n, s in score.items() if n not in seeds), key=lambda x: (-x[1], x[0]))
    return ranked, via, lost


def short(m, nid):
    kind, _, key = nid.partition(":")
    return f"{m.nodes[nid]['kind'] if nid in m.nodes else kind} {key}"


def describe(m, nid, cut=90):
    n = m.nodes[nid]
    bits = [short(m, nid)] + ([n["l0"][:cut]] if n["l0"] else [])
    if n["kind"] in ("spec", "feat"):
        bits += [x for x in (n["status"][:40], "Tech " + n["extra"]["tech"] if n["extra"].get("tech") else "") if x]
    return " \u00b7 ".join(bits)


def members(m, nid, label):
    return sorted(o for o, t, lab, *_ in m.adj[nid] if lab == label)


def detail(m, nid, level):
    """The indented lines a node adds at level 1 or 2."""
    n, x, out = m.nodes[nid], m.nodes[nid]["extra"], []
    if level < 1:
        return out
    if n["kind"] == "spec":
        rules = x.get("rules", [])
        cap = 5 if level == 1 else 12
        out += [f"try: {x['tried']}"] if x.get("tried") else []
        out += [f"rule {i}: {r}" for i, r in enumerate(rules[:cap], 1)]
        if len(rules) > cap:
            out.append(f"+{len(rules) - cap} more rules (--level 2)")
        if level == 2:
            out += [f"{n['path']}:{a}-{b} {title}" for title, a, b in x.get("sections", [])]
    elif n["kind"] in ("file", "test"):
        names = [what for _, what in x.get("outline", [])]
        if level == 1 and names:
            out.append(", ".join(w.split()[-1] for w in names[:12]) + (f" +{len(names) - 12}" if len(names) > 12 else ""))
        elif level == 2:
            out += [f"{n['path']}:{line} {what}" for line, what in x.get("outline", [])[:15]]
    elif n["kind"] == "dec":
        out.append(x.get("text", "")[:240 if level == 1 else 600])
    elif n["kind"] in ("mod", "feat"):
        got = members(m, nid, "has" if n["kind"] == "mod" else "has-batch")
        out.append(f"{len(got)} members: " + ", ".join(short(m, o) for o in got[:8]))
    elif n["kind"] == "area":
        out.append(f"maps {len(members(m, nid, 'maps'))} files ({n['path']})")
    elif n["kind"] == "q":
        out.append(f"{n['status']} ({n['path']}:{x.get('line')})")
    return [ln for ln in out if ln]


def reads(m, nid, topic=None):
    """Level 3: what to read for a node, as `read path:a-b (what)` lines; a code file's best symbols by their line
    ranges in the working tree as it is now (`kmap_lines`, given the topic's terms)."""
    n, x = m.nodes[nid], m.nodes[nid]["extra"]
    if not n["path"]:
        return []
    if n["kind"] == "spec":
        keep = [s for s in x.get("sections", []) if s[0].lower().startswith(("what must be true", "how to build"))]
        return [f"read {n['path']}:{a}-{b} ({title})" for title, a, b in keep] or [f"read {n['path']}"]
    if n["kind"] in ("file", "test"):
        got = ranges(m.root, n["path"], topic) if topic and getattr(m, "root", None) else None
        if got:
            return [f"read {n['path']}:{got}"]
        lines = x.get("lines")
        if lines and lines > 300:
            return [f"read {n['path']} ({lines} lines: tools/map.py first, then the range)"]
        return [f"read {n['path']}" + (f" ({lines} lines)" if lines else "")]
    return [f"read {n['path']}:{x['line']}" if x.get("line") else f"read {n['path']}"]


def chain(m, via, seeds, nid):
    hops, cur = [], nid
    while cur not in seeds and cur in via and len(hops) < 4:
        frm, label, conf = via[cur]
        hops.append((frm, label + MARK.get(conf, ""), cur))
        cur = frm
    return " ".join([short(m, hops[-1][0])] + [f"-{label}-> {short(m, to)}" for _, label, to in reversed(hops)])


def query(m, a, sf):
    """The answer to `kmap.py <topic>`; `sf` is the session's seen-file or None."""
    from kmap_list import folder_of, hint
    state, ids = resolve(m, a.topic)
    if state != "ok":
        if folder_of(m, a.topic):
            print(f"hint: this looks like an inventory question: kmap.py list {a.topic} --group (a folder, no node)")
        return refuse(state, a.topic, ids)
    seed = ids[0]
    ranked, via, lost = flow(m, ids, a.depth, a.role, set(a.types.split(",")) if a.types else None)
    inventory = hint(m, seed, ranked)
    if inventory and not a.json:
        print(inventory)
    seen = json.loads(sf.read_text(encoding="utf-8")) if sf and sf.is_file() else {}
    asks, shown_at = seen.get("asks", {}), seen.get("nodes", {})
    key = f"{seed}#{a.level}"
    asks[key] = asks.get(key, 0) + 1
    budget = a.budget or BUDGET[a.level]
    loop = (f"LOOP: {seed} asked {asks[key]} times at level {a.level} this session: read its --level 3 list, or "
            "kmap.py miss <topic> <file> --why \"<what you needed>\"") if asks[key] >= 3 else ""
    if a.json:
        shown = ranked[:budget]
        print(json.dumps({"seed": seed, "node": m.nodes[seed], "reached": len(ranked), "lost": round(lost, 3),
                          "loop": loop, "hint": inventory, "nodes": [{"id": n, "score": round(s, 3), "l0": m.nodes[n]["l0"],
                                                   "via": via[n][0], "label": via[n][1]} for n, s in shown]}, indent=1))
        shown_ids = [n for n, _ in shown]
        paths = [m.nodes[n]["path"] for n in [seed] + shown_ids]
    elif a.level == 3:
        shown_ids, paths = level3(m, seed, ranked, budget, loop)
    else:
        shown_ids, paths = ranked_lines(m, a, ids, ranked, via, lost, budget, loop, shown_at)
    if sf:
        sf.parent.mkdir(parents=True, exist_ok=True)
        for nid in shown_ids:
            shown_at[nid] = max(shown_at.get(nid, -1), a.level)
        seen = {"asks": asks, "nodes": shown_at, "paths": sorted(set(seen.get("paths", [])) | {p for p in paths if p})}
        sf.write_text(json.dumps(seen), encoding="utf-8")
    return 0


def level3(m, seed, ranked, budget, loop):
    lines, shown_ids, paths = [loop] if loop else [], [], []
    topic = terms(m, seed, getattr(m, "root", None))
    for nid in [seed] + [n for n, _ in ranked]:
        for r in reads(m, nid, topic):
            if r not in lines and len(lines) < budget - 1:
                lines.append(r)
                paths.append(m.nodes[nid]["path"])
                shown_ids.append(nid)
    lines.append(f"-- {sum(ln.startswith('read ') for ln in lines)} reads for {seed}, from the top of {len(ranked)}"
                 " related; read by range, and the code wins over any doc")
    print("\n".join(lines))
    return shown_ids, paths


def ranked_lines(m, a, ids, ranked, via, lost, budget, loop, shown_at):
    seed = ids[0]
    lines = ([loop] if loop else []) + [describe(m, seed, 120) + (
        " \u00b7 not " + ", ".join(m.nodes[seed]["extra"]["not"]) if m.nodes[seed]["extra"].get("not") else "")]
    lines += [" " + d for d in detail(m, seed, a.level)]
    paths, shown_ids = [m.nodes[seed]["path"]], []
    for nid, s in ranked:
        frm, label, conf = via[nid]
        how = label + MARK.get(conf, "") + ("" if frm in ids else f" of {short(m, frm)}")
        if shown_at.get(nid, -1) >= a.level:
            row = [f"{s:4.2f} (seen) {short(m, nid)}"]
        else:
            row = [f"{s:4.2f} {describe(m, nid)} \u00b7 via {how}"] + ["      " + d for d in detail(m, nid, a.level)]
            if a.why and frm not in ids:
                row.append("      why: " + chain(m, via, ids, nid))
        if len(lines) + len(row) > budget - 1:
            row = row[:1]                     # no room for its facts: its line alone
            if len(lines) + 1 > budget - 1:
                break
        lines += row
        shown_ids.append(nid)
        paths.append(m.nodes[nid]["path"])
    if not ranked:
        leaf = reads(m, seed)
        lines.append(f"-- leaf: nothing links here above {THETA}; " + (leaf[0] if leaf else "read the code"))
    else:
        lines.append(f"-- {len(shown_ids)} shown of {len(ranked)} reached (depth {a.depth}); "
                     f"{len(ranked) - len(shown_ids)} cut by the budget, mass {lost:.2f} fell under {THETA}. next: "
                     + ("--level 1 for facts \u00b7 " if a.level == 0 else "")
                     + "--level 3 for what to read \u00b7 kmap.py miss <topic> <file> --why \"..\" when the map lacked it")
    print("\n".join(lines))
    return shown_ids, paths


def find_path(m, a_id, b_id, hops=4):
    """The strongest chain of at most `hops` links, scored like one strand of the flow (decay, split by the node's
    degree, hub damping): (score, [nodes], [labels])."""
    best = {a_id: (1.0, [a_id], [])}
    frontier = [a_id]
    for _ in range(hops):
        nxt = []
        for n in frontier:
            p, nodes, labels = best[n]
            split = p / math.sqrt(max(1, m.deg.get(n, 1)))
            for o, t, label, w, conf in m.adj.get(n, []):
                q = ALPHA * split * w * damp(m, o)
                if o not in best or q > best[o][0]:
                    best[o] = (q, nodes + [o], labels + [label + MARK.get(conf, "")])
                    nxt.append(o)
        frontier = nxt
    return best.get(b_id)


def cmd_path(m, a):
    got = []
    for t in (a.a, a.b):
        state, ids = resolve(m, t)
        if state != "ok":
            return refuse(state, t, ids)
        got.append(ids[0])
    found = find_path(m, *got)
    if not found or found[0] < THETA:
        print(f"no path above {THETA} in 4 hops between {short(m, got[0])} and {short(m, got[1])}: unrelated for this "
              "job. If they are related: kmap.py miss <a> <b> --why \"..\"")
        return 0
    p, nodes, labels = found
    print(f"{p:.3f} " + short(m, nodes[0]) + "".join(f" -{lab}-> {short(m, n)}" for lab, n in zip(labels, nodes[1:])))
    hubs = [short(m, n) for n in nodes[1:-1] if m.deg.get(n, 0) > HUB_MIN]
    if p < WEAK:
        print(f"weak (under {WEAK}): " + (f"only through the hub {', '.join(hubs)}; " if hubs else "")
              + "probably unrelated for this job")
    return 0
