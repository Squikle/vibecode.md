"""Is each role worth its cost? The value half of `tools/work/role-telemetry.py --value`, no model:

- **Cost per role per week:** runs, tokens and $ (the database's cost, else list prices: `role_data.py`).
- **Spec quality:** from the reviews (`.work/<id>/review*.md`), per week: the `[spec-gap]` share of the cause-tagged
  findings (all, and blockers + majors), `CHANGES NEEDED` rounds before a green per batch. The yardstick for the
  architect's gate and the plan review: worth it when spec gaps and fix rounds fall by more than they cost (a fix
  round = the median builder session $).
- **Role output:** per architect, plan-review and product session, from its own tool calls: findings it wrote, spec
  edits, `How to build` edits, commits; the product lead's accepted/rejected marks where a pass recorded them.
- **The rating:** per role in `tools/work/roles.json`: runs this week, $ against its weekly budget, its value
  metric against its threshold, and a verdict `keep` / `trim` / `cut candidate`, or `unknown` when a metric has too
  little data (never a guess); also written to the vault as `live/Roles.md`.
"""
import collections
import json
import pathlib
import re
import statistics
import subprocess
import time

import role_data

TAG = re.compile(r"\[(spec-gap|no-test|timing|at-limit|drift|other)\]", re.I)
SEV = re.compile(r"\b(blocker|must-fix|major|minor|nit)\b", re.I)
VERDICT = re.compile(r"Verdict\W{0,6}(CHANGES NEEDED|GREEN WITH NOTES|GREEN|READY TO MERGE|APPROVED?)", re.I)
BATCH = [re.compile(p, re.I) for p in (r"branch\s+`?([\w./-]+?)`?[\s,.]", r"Review:?\s+`?([\w.-]+?)`?[\s,@(]",
                                        r"batch\s+`?([\w.-]+?)`?[\s,.(]")]
FINDING = re.compile(r"^\s*(?:\d+[.)]|[-*]|\|)\s.*?(\b(blocker|must-fix|major|minor|nit)\b|\[(fidelity|missing|conflict|"
                     r"arch|testable|order|spec-gap|no-test|timing|at-limit|drift|other)\])", re.I)
MIN_N = {"spec_gap_share": 15, "rounds_to_green": 5, "majors_per_changes_round": 5, "usd_per_merged_batch": 5,
         "usd_per_merge": 5, "coord_usd_per_merged_batch": 5, "usd_per_run": 3}
NO_SOURCE = {"gate_net_usd": "no batch is marked as planned through the gate yet; needs 10",
             "spec_gap_share_groomed": "reviews do not say which batches were pre-groomed",
             "doc_kb_cut_per_usd": "librarian commits are not joined to sessions yet",
             "look_fix_rounds": "no look-review rounds are recorded per batch"}


def week_of(ms):
    return time.strftime("%G-W%V", time.localtime((ms or 0) / 1000))


def review_files(work):
    work = pathlib.Path(work)
    return [p for pat in ("*/review*.md", "*/*/review*.md") for p in work.glob(pat) if not p.name.startswith("reviewer")]


def parse_review(p):
    text = p.read_text(encoding="utf-8", errors="replace")
    head = "\n".join(text.splitlines()[:6]) + " "
    batch = next((m.group(1) for rx in BATCH if (m := rx.search(head))), None)
    if not batch:
        m = re.match(r"review-(.+?)(-\d+|-r\d+)?\.md$", p.name)
        batch = m.group(1) if m and not m.group(1).isdigit() else p.parent.name
    v = VERDICT.search(text)
    word = (v.group(1) if v else "CHANGES NEEDED" if "CHANGES NEEDED" in text else
            "READY TO MERGE" if "READY TO MERGE" in text else "").upper()
    verdict = "changes" if word.startswith("CHANGES") else "green" if word else "unknown"
    found = []
    for line in text.splitlines():
        tags = TAG.findall(line)
        if tags:
            sev = SEV.search(line)
            found += [((sev.group(1).lower() if sev else "unknown"), t.lower()) for t in tags]
    majors = sum(1 for line in text.splitlines() if re.match(r"^\s*(\d+[.)]|[-*])\s+\**(blocker|must-fix|major)\b", line, re.I))
    return {"path": str(p), "batch": batch.strip("`.,"), "verdict": verdict, "findings": found, "majors": majors,
            "mtime": int(p.stat().st_mtime * 1000)}


def reviews(work):
    return sorted((parse_review(p) for p in review_files(work)), key=lambda r: r["mtime"])


def batches(revs):
    """{batch: {"rounds": CHANGES before the first green (None: no green yet), "green_at": ms, ...}}."""
    out = {}
    for r in revs:
        b = out.setdefault(r["batch"], {"changes": 0, "rounds": None, "green_at": None, "reviews": 0, "majors": 0,
                                        "spec_gap": 0})
        b["reviews"] += 1
        b["majors"] += r["majors"]
        b["spec_gap"] += sum(1 for _, t in r["findings"] if t == "spec-gap")
        if r["verdict"] == "changes":
            b["changes"] += 1
        elif r["verdict"] == "green" and b["green_at"] is None:
            b["green_at"] = r.get("at", r["mtime"])
    for b in out.values():  # every CHANGES of a batch that went green: file times are not the order they were written
        b["rounds"] = b["changes"] if b["green_at"] else None
    return out


def spec_quality(revs):
    """Per week: tagged findings, the spec-gap ones, the blocker/major subset, reviews, CHANGES rounds, batches."""
    wk = collections.defaultdict(lambda: collections.Counter())
    for r in revs:
        w = wk[week_of(r.get("at", r["mtime"]))]
        w["reviews"] += 1
        w["changes"] += r["verdict"] == "changes"
        w["majors_in_changes"] += r["majors"] if r["verdict"] == "changes" else 0
        for sev, tag in r["findings"]:
            w["tagged"] += 1
            w["spec_gap"] += tag == "spec-gap"
            if sev in ("blocker", "must-fix", "major"):
                w["major_tagged"] += 1
                w["major_spec_gap"] += tag == "spec-gap"
    rounds = collections.defaultdict(list)
    for b in batches(revs).values():
        if b["green_at"]:
            rounds[week_of(b["green_at"])].append(b["rounds"])
    return {w: dict(c, rounds=rounds.get(w, [])) for w, c in sorted(wk.items())}


def howto_split(root, revs):
    """Batches whose spec has `## How to build` against the rest: per batch, majors, spec gaps and CHANGES rounds."""
    specs = {}
    for p in list((root / "docs" / "specs").glob("*.md")) + list((root / "docs" / "specs" / "done").glob("*.md")):
        specs[p.stem] = bool(re.search(r"(?m)^## How to build", p.read_text(encoding="utf-8", errors="replace")))
    groups = {True: [], False: []}
    for name, b in batches(revs).items():
        if name in specs:
            groups[specs[name]].append(b)
    res = {"specs": len(specs), "with_howto": sum(specs.values())}
    for k, label in ((True, "how_to_build"), (False, "rest")):
        g = groups[k]
        res[label] = {"batches": len(g), "majors_per_batch": _mean([b["majors"] for b in g]),
                      "spec_gap_per_batch": _mean([b["spec_gap"] for b in g]),
                      "changes_per_batch": _mean([b["changes"] for b in g])}
    return res


def _mean(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


def role_output(db, sess):
    """Per architect / plan-review / product session: findings written, spec edits, How to build edits, commits."""
    out = {}
    for sid, s in sess.items():
        if s["role"] not in ("architect", "plan-review", "product"):
            continue
        o = {"role": s["role"], "title": s["title"][:50], "usd": s["usd"], "kind": s["kind"], "findings": 0,
             "spec_edits": 0, "howto_edits": 0, "files": set(), "commits": 0, "created": s["created"]}
        for (data,) in db.execute("select data from session_part where session_id = ? and type = 'tool'", (sid,)):
            try:
                p = json.loads(data)
            except ValueError:
                continue
            st, tool = p.get("state") or {}, p.get("name")
            inp = st.get("input") if isinstance(st.get("input"), dict) else {}
            if st.get("status") != "completed":
                continue
            if tool in ("edit", "write", "patch"):
                path = str(inp.get("path") or inp.get("filePath") or "").replace("\\", "/")
                text = str(inp.get("content") or inp.get("newString") or inp.get("patch") or "")
                o["files"].add(path)
                o["findings"] += sum(1 for line in text.splitlines() if FINDING.match(line))
                if "/docs/specs/" in "/" + path:
                    o["spec_edits"] += 1
                    o["howto_edits"] += "How to build" in text
            elif tool in ("shell", "bash") and re.search(r"\bgit\s+commit\b", inp.get("command") or ""):
                o["commits"] += 1
        o["files"] = len(o["files"])
        out[sid] = o
    return out


def decisions_recorded(work):
    """(files, accepted, rejected): the product lead's marks on plan-review findings, where a pass wrote them."""
    files = list(pathlib.Path(work).glob("plan/*/*.md"))
    acc = rej = 0
    for p in files:
        t = p.read_text(encoding="utf-8", errors="replace")
        acc += len(re.findall(r"\baccepted\b", t, re.I))
        rej += len(re.findall(r"\brejected\b", t, re.I))
    return len(files), acc, rej


MERGED = re.compile(r"^(?:Merge branch '([\w./-]+)'|Merge ([\w./-]+):|merges: ([\w./-]+) )")


def merges(root):
    """[(ms, batch)] of the merges that landed on main (first parent: not the `merge main into x` ones)."""
    try:
        out = subprocess.run(["git", "-C", str(root), "log", "main", "--first-parent", "--merges", "--format=%ct %s"],
                             capture_output=True, text=True, encoding="utf-8", timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    got = []
    for line in out.splitlines():
        ts, _, subject = line.partition(" ")
        m = MERGED.match(subject)
        got.append((int(ts) * 1000, next((g for g in m.groups() if g), None) if m else None))
    return got


def cost_by_week(sess, by_week):
    """{role: {week: runs, tokens (by the week a session started), api $ and list $ (by the week of each request)}}."""
    wk = collections.defaultdict(lambda: collections.defaultdict(lambda: {"runs": 0, "tokens": 0, "api": 0.0, "list": 0.0}))
    for sid, s in sess.items():
        c = wk[s["role"]][week_of(s["created"])]
        c["runs"] += 1
        c["tokens"] += s["tokens"]
        weeks = by_week.get(sid) or {week_of(s["created"]): {"api": s["api_usd"], "list": s["list_usd"]}}
        for w, sp in weeks.items():
            wk[s["role"]][w]["api"] += sp.get("api", 0.0)
            wk[s["role"]][w]["list"] += sp.get("list", 0.0)
    return wk


def helper_use(sess):
    """{(role, week): (runs, helper spawns)}: a `helper` child counts for its parent's role, so under-use shows."""
    out = collections.defaultdict(lambda: [0, 0])
    for s in sess.values():
        out[(s["role"], week_of(s["created"]))][0] += 1
        p = sess.get(s["parent"])
        if s["role"] == "helper" and p:
            out[(p["role"], week_of(s["created"]))][1] += 1
    return out


def metrics(spend_split, quality, this_week, merges, revs):
    """{metric: (value or None, n, note)} for this week; `spend_split`: role_data.week_spend()."""
    q = quality.get(this_week, {})
    spend = collections.defaultdict(lambda: [0, 0.0])
    for role, s in spend_split.items():
        spend[role] = [s["runs"], s["api"] + s["list"]]
    m = {"spec_gap_share": (q.get("spec_gap", 0) / q["tagged"] if q.get("tagged") else None, q.get("tagged", 0),
                            "share of cause-tagged review findings"),
         "rounds_to_green": (_mean(q["rounds"]) if q.get("rounds") else None, len(q.get("rounds", [])),
                             "mean CHANGES rounds before a green"),
         "majors_per_changes_round": (q.get("majors_in_changes", 0) / q["changes"] if q.get("changes") else None,
                                      q.get("changes", 0), "blockers + majors per CHANGES round it caused")}
    for metric, role in (("usd_per_merged_batch", "builder"), ("usd_per_merge", "integrator"),
                         ("coord_usd_per_merged_batch", "coordinator")):
        m[metric] = (spend[role][1] / merges if merges else None, merges or 0, f"{role} $ per merge to main")
    m["_spend"], m["_split"] = spend, spend_split
    return m


def value_of(metric, role, m):
    if metric == "usd_per_run":
        runs, usd = m["_spend"][role]
        return (usd / runs if runs else None), runs, "$ per run"
    if metric in NO_SOURCE:
        return None, 0, NO_SOURCE[metric]
    return m.get(metric, (None, 0, "not computed"))


def rate(reg, m):
    """Per registry role: (role, mode, runs, usd, budget, [metric lines], verdict)."""
    rows = []
    for name, r in reg["roles"].items():
        runs, usd = m["_spend"][name]
        cap = r["budget"]["week"]
        over = usd > cap
        lines, known, good = [], True, True
        for v in r["value"]:
            val, n, note = value_of(v["metric"], name, m)
            thin = val is None or n < MIN_N.get(v["metric"], 1)
            why = ", thin" if thin else ""
            if not thin and v["metric"].endswith("_share") and 0 < val < 1:  # inside one standard error: no call
                thin = abs(val - v["threshold"]) < (val * (1 - val) / n) ** 0.5
                why = ", within noise" if thin else ""
            ok = val is not None and (val <= v["threshold"] if v["good"] == "down" else val >= v["threshold"])
            arrow = "<=" if v["good"] == "down" else ">="
            shown = "-" if val is None else f"{val:.2f}"
            lines.append(f"{v['metric']} {shown} (want {arrow} {v['threshold']:g}; n={n}{why})")
            known &= not thin
            good &= ok
        if r["mode"] == "off":
            verdict = "off"
        elif not runs or not known:
            verdict = "unknown" + (" (over budget)" if over else "")
        elif good:
            verdict = "trim" if over else "keep"
        else:
            verdict = "cut candidate" if over else "trim"
        split = m.get("_split", {}).get(name, {})
        rows.append((name, r["mode"], runs, split.get("api", 0.0), split.get("list", usd), cap, lines, verdict))
    return rows


def rating_md(rows, stamp):
    out = ["| Role | Mode | Runs this week | API $ | Subscription at list $ | Of the weekly budget | Value vs threshold "
           "| Verdict |", "|---|---|---:|---:|---:|---|---|---|"]
    for name, mode, runs, api, lst, cap, lines, verdict in rows:
        out.append(f"| {name} | {mode} | {runs} | {api:.0f} | {lst:.0f} | {(api + lst) / cap:.0%} of ${cap:g} | "
                   f"{'; '.join(lines)} | **{verdict}** |")
    return "\n".join(out + ["", f"Rated {stamp}. `unknown` = too little data or no source yet (never a guess); "
                            "`trim` = a cheaper model or fewer runs; `cut candidate` = over budget and under its value."])


BASELINE = None  # ([spec-gap], all cause-tagged findings) counted before the gate came in: your own, when you have one


def value_section(db, reg, work, root, sessions_since, week_start, now=None):
    """(markdown, json) of the value half: the rating, cost per role per week, spec quality, How to build, output."""
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now or time.time()))
    this_week = week_of(week_start)
    since = week_start - 3 * 7 * 86400 * 1000
    hist = sessions_since(since)
    revs = reviews(work) if work and pathlib.Path(work).is_dir() else []
    landed = merges(root)
    at = {b: t for t, b in landed if b}
    for r in revs:  # a review counts in the week its batch merged (file times moved with the folder), else its own
        r["at"] = at.get(r["batch"], r["mtime"])
    quality = spec_quality(revs)
    n_merges = sum(1 for t, b in landed if t >= week_start and b)  # a named branch: a batch, not a `Merge commit`
    m = metrics(role_data.week_spend(db, week_start), quality, this_week, n_merges, revs)
    rows = rate(reg, m)
    lane_usd = [s["usd"] for s in hist.values() if s["role"] == "builder" and s["usd"] > 0]
    fix_round = statistics.median(lane_usd) if lane_usd else None
    md = ["## Rating: is each role worth its cost?", "", rating_md(rows, stamp), "",
          "## Cost per role per week", "", "| Role | " + " | ".join(sorted({week_of(s['created']) for s in hist.values()}))
          + " |"]
    weeks = sorted({week_of(s["created"]) for s in hist.values()})
    md.append("|---|" + "---:|" * len(weeks))
    cw = cost_by_week(hist, role_data.message_spend(db, since, set(hist), by_week=True))
    for role in sorted(cw, key=lambda r: -sum(c["api"] + c["list"] for c in cw[r].values())):
        cells = [f"{c['runs']} / {c['tokens'] / 1e6:.1f}M / ${c['api']:.0f} + ${c['list']:.0f}"
                 if (c := cw[role].get(w)) else "-" for w in weeks]
        md.append(f"| {role} | " + " | ".join(cells) + " |")
    hu = helper_use(hist)
    md += ["", "Cells: runs / tokens / API $ + subscription at list $ (this project's sessions only).", "",
           "## Helper use per role per week", "", "| Role | Week | Runs | `helper` spawns | Per run |", "|---|---|---:|---:|---:|"]
    md += [f"| {r} | {w} | {n} | {h} | {h / n:.2f} |" for (r, w), (n, h) in sorted(hu.items()) if r not in ("helper", "other")]
    md += ["",
           "## Spec quality per week", "",
           (f"Baseline before the gate: `[spec-gap]` {BASELINE[0]} of {BASELINE[1]} tagged findings "
            f"({BASELINE[0] / BASELINE[1]:.0%}). " if BASELINE else "No baseline before the gate recorded. ")
           + "A fix round = the median builder session: "
           + (f"${fix_round:.2f}" if fix_round else "unknown") + f" (n={len(lane_usd)}).", "",
           "| Week | Reviews | Tagged | `[spec-gap]` | Share | Blocker+major tagged | of them spec-gap | CHANGES | "
           "Batches green | Mean rounds to green |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for w, q in quality.items():
        share = f"{q.get('spec_gap', 0) / q['tagged']:.0%}" if q.get("tagged") else "-"
        med = _mean(q["rounds"]) if q["rounds"] else "-"
        md.append(f"| {w} | {q.get('reviews', 0)} | {q.get('tagged', 0)} | {q.get('spec_gap', 0)} | {share} | "
                  f"{q.get('major_tagged', 0)} | {q.get('major_spec_gap', 0)} | {q.get('changes', 0)} | "
                  f"{len(q['rounds'])} | {med} |")
    hs = howto_split(root, revs)
    md += ["", "## Specs with a How to build against the rest", "",
           f"{hs['with_howto']} of {hs['specs']} specs have `## How to build`.", "",
           "| Specs | Batches reviewed | Majors per batch | `[spec-gap]` per batch | CHANGES per batch |",
           "|---|---:|---:|---:|---:|"]
    for k in ("how_to_build", "rest"):
        g = hs[k]
        md.append(f"| {k} | {g['batches']} | {g['majors_per_batch']} | {g['spec_gap_per_batch']} | {g['changes_per_batch']} |")
    out = role_output(db, sessions_since(0))
    files, acc, rej = decisions_recorded(work) if work else (0, 0, 0)
    md += ["", "## Output of the architect, plan-review and product sessions", "",
           "| Session | Role | Title | API $ | List $ | Findings written | Spec edits | How to build edits | Files | Commits |",
           "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for sid, o in sorted(out.items(), key=lambda kv: kv[1]["created"] or 0):
        api = o["usd"] if o["kind"] == "db" else 0.0
        md.append(f"| `{sid}` | {o['role']} | {o['title']} | {api:.2f} | {o['usd'] - api:.2f} | "
                  f"{o['findings']} | {o['spec_edits']} | {o['howto_edits']} | {o['files']} | {o['commits']} |")
    md += ["", f"Product lead's accepted / rejected marks on plan-review findings: {acc} / {rej} in {files} pass files"
           + (" (none recorded yet: `.work/plan/<pass>/` holds no pass)" if not files else "") + "."]
    js = {"rating": [dict(zip(("role", "mode", "runs", "api_usd", "list_usd", "budget", "value", "verdict"), r)) for r in rows],
          "helper_use": [{"role": r, "week": w, "runs": n, "helper": h} for (r, w), (n, h) in sorted(hu.items())],
          "quality": quality, "howto": hs, "fix_round_usd": fix_round, "merges_this_week": n_merges, "output": out,
          "cost_by_week": {r: dict(v) for r, v in cw.items()}}
    return "\n".join(md), js, rows, stamp


def vault_note(vault, rows, stamp):
    p = pathlib.Path(vault) / "live" / "Roles.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    body = ["# Roles: worth their cost?", "", "Each role's week so far: runs, $ against its budget (subscription "
            "sessions at list prices), its value metric against its threshold, and a verdict. Set a role with "
            "`python -I -X utf8 tools/work/roles.py set <role> mode=off|on|on-request model=... budget.week=...`.",
            "", rating_md(rows, stamp), ""]
    p.write_text("\n".join(body), encoding="utf-8", newline="\n")
    return p
