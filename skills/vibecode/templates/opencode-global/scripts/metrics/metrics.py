#!/usr/bin/env python3
"""Append-only usage metrics and an optional markdown chart page. Zero LLM, stdlib only (Windows, macOS, Linux).

python -I -X utf8 metrics.py [--work <project>/.work] [--repo <checkout>] [--page <file.md>] [--provider <id>]...
                             [--window-pct <N>] [--db <opencode.db>] [--data <dir>]
Always exits 0; one line per run in <data>/metrics.log. Run it from a no-model shell automation every 10 minutes.
Never have an agent read the data or the page: they are for the owner's eyes, and reading them costs tokens.

Data (append-only JSONL, the host in the name so other machines' files merge at read), in $METRICS_DATA, else
$XDG_DATA_HOME/metrics, else ~/.local/share/metrics:
  limits.<host>.jsonl  one row per new pacer sample (the opt-in pacer's plugin-state.json or state.json, the newer):
                       5h and week %, their resets, per-model weekly %, the mode.
  usage.<host>.jsonl   hour x model x role (top | child) token deltas from OpenCode's database (read-only; completed
                       assistant messages; `--provider` keeps only those providers). Rows add up at read.
  lanes.<host>.jsonl   (hour, child session) pairs: active lanes per hour.
  gates.<host>.jsonl   one row per CI verdict file (`<work>/ci/*.json`): time, verdict, seconds.
  state.<host>.json    cursors. Merges are counted from git at render time (git is already append-only).
Weighted units = in + 1.25 cache write + 0.1 cache read + 5 out (API price ratios, not a plan's real formula).
Weeks start at the reset the pacer reports. The page is a view, rewritten each run; its charts are the Obsidian
"Charts" plugin's code blocks (plain text elsewhere)."""
import argparse, glob, json, os, socket, sqlite3, subprocess, sys, time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
XDG = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share")
HOST = socket.gethostname().split(".")[0].lower() or "host"
HOUR, WEEK, M = 3600 * 1000, 7 * 86400, 1e6
FIRST_DAYS = 14  # how far back the first run reads the database


def wu(i, o, cr, cw):
    return (i or 0) + 1.25 * (cw or 0) + 0.1 * (cr or 0) + 5 * (o or 0)


# ---------- files ----------
def append(data, name, rows):
    if rows:
        with open(data / f"{name}.{HOST}.jsonl", "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))


def read_all(data, name):
    out = []
    for p in sorted(glob.glob(str(data / f"{name}.*.jsonl"))):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------- collectors (each returns the rows it added) ----------
def latest_pace(pacer):
    """The newest usage sample: the plugin's `pace` in plugin-state.json or `pacer.py status`'s state.json."""
    got = [(read_json(pacer / "plugin-state.json") or {}).get("pace"), read_json(pacer / "state.json")]
    got = [g for g in got if isinstance(g, dict) and g.get("at") and (g.get("usage") or {}).get("d7")]
    return max(got, key=lambda g: g["at"]) if got else None


def collect_limits(st, data, pacer):
    pace = latest_pace(pacer)
    if not pace or pace["at"] == st.get("lim_at"):
        return 0
    st["lim_at"] = pace["at"]
    u = pace["usage"]
    h5, d7 = u.get("h5") or {}, u["d7"]
    append(data, "limits", [{"t": round(pace["at"], 1), "h5": h5.get("pct"), "h5r": h5.get("reset"), "d7": d7.get("pct"),
                             "d7r": d7.get("reset"), "sc": u.get("scoped") or {}, "mode": pace.get("mode")}])
    return 1


def collect_usage(st, data, db, providers, now_ms):
    first = st.get("cur") is None
    cur = st.get("cur") or now_ms - FIRST_DAYS * 86400 * 1000
    seen = st.setdefault("seen", {})
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        parent = dict(con.execute("select id, parent_id from session_v2"))
        q = ("select id, session_id, time_created, time_updated, json_extract(data,'$.model.id'), "
             "json_extract(data,'$.model.providerID'), json_extract(data,'$.tokens.input'), "
             "json_extract(data,'$.tokens.output'), json_extract(data,'$.tokens.cache.read'), "
             "json_extract(data,'$.tokens.cache.write') from session_message where type='assistant' "
             "and json_extract(data,'$.time.completed') is not null and time_updated >= ?")
        rows = con.execute(q, (cur if first else cur - 2 * HOUR,)).fetchall()  # 2 h back: late completions
    finally:
        con.close()
    agg, lanes, top, lh = {}, set(), cur, st.setdefault("lh", {})
    for mid, sid, tc, tu, model, prov, i, o, cr, cw in rows:
        top = max(top, tu)
        if mid in seen or (providers and prov not in providers):
            continue
        seen[mid] = tu
        h, role = tc // HOUR * HOUR, ("child" if parent.get(sid) else "top")
        a = agg.setdefault((h, model or "?", role), [0, 0, 0, 0, 0])
        for k, v in enumerate((i, o, cr, cw, 1)):
            a[k] += v or 0
        if role == "child" and sid not in lh.get(str(h), []):
            lanes.add((h, sid))
    append(data, "usage", [{"h": h, "m": m, "r": r, "i": a[0], "o": a[1], "cr": a[2], "cw": a[3], "n": a[4]}
                           for (h, m, r), a in sorted(agg.items())])
    append(data, "lanes", [{"h": h, "s": s} for h, s in sorted(lanes)])
    for h, s in lanes:
        lh.setdefault(str(h), []).append(s)
    st["lh"] = {k: v for k, v in lh.items() if int(k) >= now_ms // HOUR * HOUR - 3 * HOUR}
    st["seen"] = {k: v for k, v in seen.items() if v >= top - 2 * HOUR}
    st["cur"] = top
    return len(agg)


def collect_gates(st, data, work):
    done, rows = st.setdefault("gates", {}), []
    for f in sorted((Path(work) / "ci").glob("*.json")):
        mt = int(f.stat().st_mtime)
        if done.get(f.name) == mt:
            continue
        done[f.name] = mt
        d = read_json(f)
        if isinstance(d, dict) and d.get("verdict"):
            rows.append({"t": mt, "sha": f.stem, "v": d["verdict"], "s": d.get("seconds")})
    append(data, "gates", rows)
    return len(rows)


def merges(repo):
    if not repo:
        return []
    try:
        r = subprocess.run(["git", "-C", str(repo), "log", "--merges", "--format=%ct"], capture_output=True, text=True,
                           timeout=15)
        return [int(x) for x in r.stdout.split()]
    except Exception:
        return []


# ---------- derived ----------
def week_start(t, anchor):
    """The start of the week holding `t`, weeks running from `anchor` (a reset time) in 7-day steps."""
    return anchor + ((t - anchor) // WEEK) * WEEK


def default_anchor(t):
    d = datetime.fromtimestamp(t)
    return (d.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=d.weekday())).timestamp()


def units_between(ht, t0, t1):
    tot, h = 0.0, int(t0 * 1000) // HOUR * HOUR
    while h < t1 * 1000:
        lo, hi = max(h, t0 * 1000), min(h + HOUR, t1 * 1000)
        tot += ht.get(h, 0.0) * max(0.0, hi - lo) / HOUR
        h += HOUR
    return tot


def per_day_left(d7, reset, now):
    """Week % left per day to the reset: under one 5-hour window's worth a day means the week runs short."""
    days = max((reset - now) / 86400, 1 / 24)
    return (100 - d7) / days


# ---------- page ----------
def chart(kind, labels, series, stacked=False, cap=""):
    y = ["```chart", f"type: {kind}", f"labels: {json.dumps(labels)}", "series:"]
    for title, vals in series:
        y += [f"  - title: {json.dumps(title)}", f"    data: {json.dumps(vals)}"]
    y += ["width: 100%", "labelColors: false", "fill: false", "beginAtZero: true", "tension: 0.2", "spanGaps: true"]
    if stacked:
        y.append("stacked: true")
    y.append("```")
    return (f"*{cap}*\n" if cap else "") + "\n".join(y) + "\n"


def r2(x):
    return None if x is None else round(x, 2)


def render(data, repo=None, window_pct=None, now=None):
    now = time.time() if now is None else now
    nowh = int(now * 1000) // HOUR * HOUR
    lim = sorted((r for r in read_all(data, "limits") if r.get("d7") is not None), key=lambda r: r["t"])
    us = read_all(data, "usage")
    anchor = next((r["d7r"] for r in reversed(lim) if r.get("d7r")), None) or default_anchor(now)
    ht, role_h = defaultdict(float), defaultdict(float)
    model_d, mix_d = defaultdict(lambda: defaultdict(float)), defaultdict(lambda: [0.0] * 4)
    day = lambda ms: datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")
    for r in us:
        w = wu(r["i"], r["o"], r["cr"], r["cw"])
        ht[r["h"]] += w
        role_h[(r["h"], r["r"])] += w
        model_d[r["m"]][day(r["h"])] += w
        for k, v in enumerate((r["cr"] * 0.1, r["cw"] * 1.25, r["o"] * 5, r["i"])):
            mix_d[day(r["h"])][k] += v
    last168 = [nowh - k * HOUR for k in range(167, -1, -1)]
    hl = lambda h: datetime.fromtimestamp(h / 1000).strftime("%m-%d %Hh")
    fmt = lambda t: datetime.fromtimestamp(t).strftime("%a %m-%d %H:%M")
    out = ["# Metrics", f"*built {datetime.fromtimestamp(now):%Y-%m-%d %H:%M} · weighted units = in + 1.25 cache write"
                        " + 0.1 cache read + 5 out (API price ratios) · M = million*", ""]

    cur = lim[-1] if lim else None
    if cur:
        ws = week_start(cur["t"], anchor)
        we = ws + WEEK
        prev = [s for s in lim if s["t"] >= max(cur["t"] - 86400, ws)]
        rate = None
        if len(prev) > 1 and prev[-1]["t"] - prev[0]["t"] >= 3 * 3600:
            rate = (prev[-1]["d7"] - prev[0]["d7"]) / ((prev[-1]["t"] - prev[0]["t"]) / 3600)
        if not rate or rate <= 0:
            rate = cur["d7"] / max(1.0, (cur["t"] - ws) / 3600)
        if rate > 0:
            eta = cur["t"] + (100 - cur["d7"]) / rate * 3600
            gap = (we - eta) / 3600
            proj = f"{fmt(eta)} vs reset {fmt(we)} · " + (f"❌ {gap:.0f} h early" if gap > 0 else "✅ lasts")
        else:
            proj = "no burn"
        left = per_day_left(cur["d7"], we, now)
        short = window_pct is not None and left < window_pct
        age = (now - cur["t"]) / 60
        wk_units = units_between(ht, ws, min(now, we)) / M
        out += ["| now | |", "|---|---|",
                f"| 5h | **{cur['h5']}%**" + (f" · resets {datetime.fromtimestamp(cur['h5r']):%H:%M}" if cur.get("h5r") else "") + " |",
                f"| week | **{cur['d7']}%** · {rate:.2f} %/h recent |",
                *[f"| {name} (week) | **{pct}%** |" for name, pct in sorted((cur.get("sc") or {}).items()) if pct is not None],
                f"| mode | {cur.get('mode') or '–'} |",
                f"| week runs out | {proj} |",
                f"| week left per day | {left:.1f} %" + (f" · one 5h window ≈ {window_pct} % · " + ("⚠️ under one a day" if short else "✅")
                                                          if window_pct is not None else "") + " |",
                f"| week tokens | {wk_units:.0f} M units" + (f" → {cur['d7'] / (wk_units / 100):.2f} % per 100 M" if wk_units > 0 else "") + " |",
                f"| last sample | {age:.0f} min ago" + (" ⚠️ stale" if age > 30 else "") + " |", ""]
    else:
        out += ["*no limit samples yet (the pacer writes them)*", ""]

    def per_hour(get):
        d = {}
        for s in lim:
            if get(s) is not None:
                d[int(s["t"] * 1000) // HOUR * HOUR] = get(s)
        return [d.get(h) for h in last168]
    names = sorted({n for s in lim for n in (s.get("sc") or {})})
    out += ["## Limits", chart("line", [hl(h) for h in last168],
                               [("5h %", per_hour(lambda s: s.get("h5"))), ("week %", per_hour(lambda s: s.get("d7"))),
                                *[(f"{n} %", per_hour(lambda s, n=n: (s.get("sc") or {}).get(n))) for n in names]],
                               cap="% used, last 7 d (gaps = no sample)")]
    if cur:
        hs = [int(ws * 1000) + k * HOUR for k in range(169)]
        used, j, best, inweek = [], 0, None, [s for s in lim if ws <= s["t"] < we]
        for h in hs:
            while j < len(inweek) and inweek[j]["t"] * 1000 < h + HOUR:
                best = inweek[j]["d7"]
                j += 1
            used.append(best if h <= now * 1000 and best is not None else None)
        out += ["## Week", chart("line", [hl(h) for h in hs], [("used %", used), ("even pace", [r2(k / 168 * 100) for k in range(169)])],
                                 cap="this week vs an even line to the reset")]

    out += ["## Tokens", chart("bar", [hl(h) for h in last168],
                               [("top-level", [r2(role_h.get((h, "top"), 0) / M) for h in last168]),
                                ("child lanes", [r2(role_h.get((h, "child"), 0) / M) for h in last168])],
                               stacked=True, cap="weighted M units per hour, last 7 d")]
    days = sorted({day(r["h"]) for r in us})[-170:]
    dl = [d[5:] for d in days]
    out += [chart("bar", dl, [(m, [r2(model_d[m].get(d, 0) / M) for d in days]) for m in sorted(model_d, key=lambda m: -sum(model_d[m].values()))],
                  stacked=True, cap="weighted M units per day by model"),
            chart("bar", dl, [(n, [r2(mix_d[d][k] / M) for d in days]) for k, n in enumerate(("cache read ×0.1", "cache write ×1.25", "output ×5", "input ×1"))],
                  stacked=True, cap="cost mix per day (weighted M units)")]

    first = min([r["h"] / 1000 for r in us] + [s["t"] for s in lim] + [now])
    wk, w = [], week_start(first, anchor)
    while w <= now:
        wk.append(w)
        w += WEEK
    rows, labels, vals = [], [], []
    for w in wk[-24:]:
        ss = [s for s in lim if w <= s["t"] < w + WEEK]
        pct = max((s["d7"] for s in ss), default=None)
        u = units_between(ht, w, ss[-1]["t"]) / M if ss else 0
        v = pct / (u / 100) if pct is not None and u > 0 else None
        labels.append(datetime.fromtimestamp(w).strftime("%m-%d"))
        vals.append(r2(v))
        rows.append(f"| {labels[-1]} | {pct if pct is not None else '–'}%{' (so far)' if w == wk[-1] else ''} | {u:.0f} | {f'{v:.2f}' if v else '–'} |")
    out += ["## Quota per token", chart("bar", labels, [("week % per 100 M units", vals)], cap="week % used per 100 M weighted units, by week"),
            "| week from | week % | M units | % per 100 M |", "|---|---|---|---|", *rows, ""]

    dd = defaultdict(lambda: [0, 0, 0])
    for t in merges(repo):
        dd[datetime.fromtimestamp(t).strftime("%Y-%m-%d")][0] += 1
    for g in read_all(data, "gates"):
        dd[datetime.fromtimestamp(g["t"]).strftime("%Y-%m-%d")][1 if g["v"] == "GREEN" else 2] += 1
    wd = [(datetime.fromtimestamp(now) - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(59, -1, -1)]
    lanes_h = defaultdict(set)
    for r in read_all(data, "lanes"):
        lanes_h[r["h"]].add(r["s"])
    out += ["## Work", chart("bar", [d[5:] for d in wd], [("merges", [dd[d][0] for d in wd]), ("gates green", [dd[d][1] for d in wd]),
                                                          ("gates other", [dd[d][2] for d in wd])], cap="merges and gate verdicts per day, last 60 d"),
            chart("bar", [hl(h) for h in last168], [("active lanes", [len(lanes_h.get(h, ())) for h in last168])], cap="child sessions active per hour, last 7 d")]
    return "\n".join(out)


def main(argv=None):
    t0, notes = time.time(), []
    ap = argparse.ArgumentParser(description="Zero-LLM usage metrics; always exits 0.")
    ap.add_argument("--data", default=os.environ.get("METRICS_DATA") or str(XDG / "metrics"))
    ap.add_argument("--pacer", default=os.environ.get("PACER_DATA") or str(HOME / ".local" / "share" / "pacer"))
    ap.add_argument("--db", default=str(XDG / "opencode" / "opencode.db"))
    ap.add_argument("--provider", action="append", default=[], help="count only this provider id (repeatable)")
    ap.add_argument("--work", help="the project's .work folder (its ci/*.json verdicts)")
    ap.add_argument("--repo", help="a checkout of the project (merges per day)")
    ap.add_argument("--page", help="the markdown page to write (a vault note, say)")
    ap.add_argument("--window-pct", type=float, help="what one 5-hour window costs of the week, in %% (measured)")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 0
    data = Path(a.data)
    try:
        data.mkdir(parents=True, exist_ok=True)
        sp = data / f"state.{HOST}.json"
        st = read_json(sp) or {}
        steps = [("limits", lambda: collect_limits(st, data, Path(a.pacer))),
                 ("usage", lambda: collect_usage(st, data, a.db, set(a.provider), int(time.time() * 1000)) if Path(a.db).is_file() else "-"),
                 ("gates", lambda: collect_gates(st, data, a.work) if a.work else "-")]
        for name, fn in steps:
            try:
                notes.append(f"{name}+{fn()}")
            except Exception as e:
                notes.append(f"{name}!{type(e).__name__}:{str(e)[:60]}")
        tmp = sp.with_suffix(".tmp")
        tmp.write_text(json.dumps(st), encoding="utf-8")
        os.replace(tmp, sp)
        if a.page:
            out = Path(a.page)
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_text(render(data, a.repo, a.window_pct), encoding="utf-8", newline="\n")
            os.replace(tmp, out)
            notes.append("page")
    except Exception as e:
        notes.append(f"FAIL {type(e).__name__}:{str(e)[:80]}")
    try:
        lp = data / "metrics.log"
        with open(lp, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {HOST} {time.time() - t0:.2f}s {' '.join(notes)}\n")
        if lp.stat().st_size > 200_000:
            lp.write_text("".join(lp.read_text(encoding="utf-8").splitlines(True)[-1000:]), encoding="utf-8")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
