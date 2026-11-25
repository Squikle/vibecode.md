"""pacer: spread the Claude subscription's usage over its 5 h and 7 d windows, and wake the coordinator.

Credentials are read-only: it reads the OAuth access token, never prints, writes or refreshes it
(a refresh rotates the token and can log Claude Code out). An expired token = skip this tick.

  python pacer.py status            one line: usage, pace lines, mode, lane cap (writes state.json)
  python pacer.py status --json     machine mode for the pacer plugin: one JSON object, writes nothing
  python pacer.py claim [--dir D]   RETIRED (v3): the plugin's /pacer toggle replaces claim.json
  python pacer.py tick [--send]     RETIRED (v3): the plugin sends; kept only for the legacy tick tests

Week toggle (v3): decide() ignores the week unless toggles.json (next to this file, or $PACER_TOGGLES)
says "week": true. Off = the week counts as on pace (BURST allowed). A week limit hit (>= 100 %) still
gives LIMITED: the API refuses calls then anyway.

Modes (the coordinator obeys the lane cap at spawn time; it never kills a running session):
  GO 5 | EASE 3 | SLOW 2 (ahead of the 5 h or the week's line) | HOLD 0 new (5 h >= 90 %, > 1 h left)
  BURST 7 (< 1 h to the 5 h reset, week on pace: a limit hit now waits < 1 h, so caches stay warm)
  LIMITED (nothing sends; the first tick after the reset wakes the coordinator)
Targeting: no hardcoded id, no "latest session". The coordinator claims itself at each heartbeat;
a send needs a claim < 75 min old (or one made < 75 min before the limit stop, for the reset wake),
the session in its project's list, and no newer coordinator-titled
session active since the claim. The message tells a wrong receiver to reply "not me".
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
# state, logs, samples, toggles: a per-user data dir, never next to this file (the installed copy lives in the
# opencode config dir, which gets synced). $PACER_DATA overrides; default ~/.local/share/pacer (Windows + macOS).
DATA = pathlib.Path(os.environ.get("PACER_DATA") or pathlib.Path.home() / ".local" / "share" / "pacer")
STATE, CLAIM, LOG, SAMPLES = (DATA / n for n in ("state.json", "claim.json", "pacer.log", "samples.jsonl"))
TOGGLES = pathlib.Path(os.environ.get("PACER_TOGGLES") or DATA / "toggles.json")
URL = "https://api.anthropic.com/api/oauth/usage"
H5, D7 = 5 * 3600, 7 * 86400
SLACK5, SLACK7 = 15.0, 10.0          # points allowed above the straight line
CLAIM_MAX_AGE = 75 * 60               # heartbeats re-claim every <= 55 min, so a retired coordinator ages out fast
IDLE_WAKE = 50 * 60                   # a 1 h cache: wake an idle coordinator before it goes cold
MODE_WAKE_IDLE = 10 * 60              # a mode that allows more lanes wakes a coordinator idle this long
LANES = {"GO": 5, "EASE": 3, "SLOW": 2, "HOLD": 0, "BURST": 7, "LIMITED": 0}


def log(line):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} {line}\n")


def load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def token():
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
    f = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude") / ".credentials.json"
    raw = None
    if f.exists():
        raw = f.read_text(encoding="utf-8")
    elif platform.system() == "Darwin":  # the Mac keeps it in the login keychain (UNVERIFIED there)
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                           capture_output=True, text=True, timeout=5)
        raw = r.stdout if r.returncode == 0 else None
    if not raw:
        return None
    o = json.loads(raw).get("claudeAiOauth") or {}
    if o.get("expiresAt", 0) / 1000 < time.time() + 60:
        return None
    return o.get("accessToken")


def usage():
    t = token()
    if not t:
        return None, "no valid token (missing or expired; Claude Code refreshes it, never us)"
    req = urllib.request.Request(URL, headers={"Authorization": "Bearer " + t, "anthropic-beta": "oauth-2025-04-20",
                                               "Accept": "application/json", "User-Agent": "pacer/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return None, f"usage HTTP {e.code}"
    except (OSError, ValueError) as e:
        return None, f"usage error {type(e).__name__}"

    def win(k):
        v = d.get(k) or {}
        ra = v.get("resets_at")
        return {"pct": float(v.get("utilization") or 0), "reset": dt.datetime.fromisoformat(ra).timestamp() if ra else None}

    scoped = [l for l in d.get("limits") or [] if l.get("kind") == "weekly_scoped"]
    return {"h5": win("five_hour"), "d7": win("seven_day"),
            "scoped": {((l.get("scope") or {}).get("model") or {}).get("display_name") or "?": l.get("percent") for l in scoped}}, None


def week_on():
    return bool(load(TOGGLES, {}).get("week", False))


def decide(u, now, week=None):
    if week is None:
        week = week_on()
    h5, d7 = u["h5"], u["d7"]
    left5 = h5["reset"] - now if h5["reset"] else H5
    left7 = d7["reset"] - now if d7["reset"] else D7
    line5 = min(100.0, max(0.0, (H5 - left5) / H5 * 100))
    line7 = min(100.0, max(0.0, (D7 - left7) / D7 * 100))
    a5, a7 = h5["pct"] - line5 - SLACK5, d7["pct"] - line7 - SLACK7
    if not week:
        a7 = -100.0  # week ignored: always on pace
    if h5["pct"] >= 100 or d7["pct"] >= 100:
        mode, why = "LIMITED", "a limit is hit"
    elif left5 <= 3600 and a7 <= 0:
        mode, why = "BURST", "under 1 h to the 5 h reset, week on pace"
    elif h5["pct"] >= 90:
        mode, why = "HOLD", f"5 h at {h5['pct']:.0f}% with {left5 / 60:.0f} min left"
    else:
        worst, name = max((a5, "5 h"), (a7, "week"))
        mode = "SLOW" if worst > 10 else "EASE" if worst > 0 else "GO"
        why = f"{name} {worst:+.0f} vs its line" if worst > 0 else "both on pace"
    return {"mode": mode, "lanes": LANES[mode], "why": why, "line5": line5, "line7": line7,
            "left5": left5, "left7": left7, "week": week}


def _clock(ts, f):
    # to the nearest minute: the API says 19:49:59.9 for a 19:50 reset (it showed 19:49, and the week 16:59)
    return dt.datetime.fromtimestamp(round(ts / 60) * 60).strftime(f)


def fmt(u, d, now):
    r5 = _clock(u["h5"]["reset"], "%H:%M") if u["h5"]["reset"] else "?"
    r7 = _clock(u["d7"]["reset"], "%a %m-%d %H:%M") if u["d7"]["reset"] else "?"
    sc = " ".join(f"{k}-week {v}%" for k, v in u["scoped"].items())
    return (f"PACE {dt.datetime.fromtimestamp(now):%H:%M} | 5h {u['h5']['pct']:.0f}% (line {d['line5']:.0f}+{SLACK5:.0f})"
            f" resets {r5} | week {u['d7']['pct']:.0f}% "
            f"{'(line %.0f+%.0f)' % (d['line7'], SLACK7) if d.get('week', True) else '(ignored)'} resets {r7}"
            f"{' | ' + sc if sc else ''} | mode {d['mode']} lanes<={d['lanes']} ({d['why']})")


def opencode():
    return shutil.which("opencode") or "opencode"


def sessions(directory):
    r = subprocess.run([opencode(), "session", "list", "--format", "json", "-n", "30"], cwd=directory,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    out = r.stdout
    try:
        return json.loads(out[out.find("["):]) if "[" in out else []
    except ValueError:
        return []


def live_claim(now, limited_since=None):
    c = load(CLAIM, None)
    if not c:
        return None, "no claim (the coordinator runs: pacer.py claim)"
    fresh = now - c["at"] <= CLAIM_MAX_AGE
    live_at_stop = limited_since is not None and limited_since - c["at"] <= CLAIM_MAX_AGE  # it was working when the limit hit
    if not (fresh or live_at_stop):
        return None, f"claim is {(now - c['at']) / 60:.0f} min old"
    ss = sessions(c["dir"])
    me = next((s for s in ss if s.get("id") == c["sid"]), None)
    if not me:
        return None, "the claimed session is not in its project's list"
    for s in ss:
        if (s["id"] != c["sid"] and s.get("created", 0) > me.get("created", 0) and s.get("updated", 0) / 1000 > c["at"]
                and re.search(r"coordinat", s.get("title", ""), re.I)):
            return None, f"newer coordinator-like session {s['id'][:12]} active since the claim: waiting for its claim"
    c["idle"] = now - me.get("updated", 0) / 1000
    return c, None


def safe(msg):
    return re.sub(r"[^A-Za-z0-9 .,:;()%+<>=/-]", " ", msg)  # survives the npm .cmd shim on Windows


def send(c, msg):
    kw = dict(cwd=c["dir"], stdin=subprocess.DEVNULL, stdout=(DATA / "send.log").open("ab"), stderr=subprocess.STDOUT)
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen([opencode(), "run", "-s", c["sid"], safe(msg)], **kw)  # returns now; the turn runs on
    return p.pid


def cmd_status(a):
    now = time.time()
    u, err = usage()
    if getattr(a, "json", False):  # the plugin's machine mode: one JSON line, no files written, never the token
        if not u:
            print(json.dumps({"ok": False, "err": err, "at": now}))
            return 1
        d = decide(u, now)
        print(json.dumps({"ok": True, "at": now, "usage": u, **d, "line": fmt(u, d, now)}))
        return 0
    if not u:
        print("PACE ?", err)
        return 1
    d = decide(u, now)
    line = fmt(u, d, now)
    STATE.write_text(json.dumps({"at": now, "usage": u, **d, "line": line}), encoding="utf-8")
    print(line)
    return 0


def cmd_claim(a):
    sid = os.environ.get("OPENCODE_SESSION_ID")
    if not sid:
        print("CLAIM FAIL: no OPENCODE_SESSION_ID in this shell")
        return 1
    d = os.path.abspath(a.dir or os.getcwd())
    if not any(s.get("id") == sid for s in sessions(d)):
        print(f"CLAIM FAIL: {sid[:16]} is not in the session list seen from {d} (pass --dir <the session's directory>)")
        return 1
    CLAIM.write_text(json.dumps({"sid": sid, "dir": d, "at": time.time()}), encoding="utf-8")
    print(f"CLAIM {sid[:16]} in {d}")
    return 0


def cmd_tick(a):
    now = time.time()
    st = load(STATE, {})
    u, err = usage()
    if not u:
        log(f"skip: {err}")
        print("TICK skip:", err)
        return 0
    d = decide(u, now)
    line = fmt(u, d, now)
    with SAMPLES.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": round(now), "p5": u["h5"]["pct"], "p7": u["d7"]["pct"], "mode": d["mode"]}) + "\n")
    pu = st.get("usage", {})
    prev5, prev7 = pu.get("h5", {}).get("pct"), pu.get("d7", {}).get("pct")
    prev_mode, last_send, lim = st.get("mode"), st.get("last_send", 0), st.get("limited_since")
    why = None
    if d["mode"] == "LIMITED":
        st["limited_since"] = lim or now
    else:
        st["limited_since"] = None  # used once, on the first tick after a stop, then gone
        reset = any(p is not None and p >= 95 and q < p - 40
                    for p, q in ((prev5, u["h5"]["pct"]), (prev7, u["d7"]["pct"])))
        c, cerr = live_claim(now, lim if reset else None)
        if reset:
            why = "RESET"
        elif c and c["idle"] >= IDLE_WAKE and now - last_send >= IDLE_WAKE:
            why = "HEARTBEAT"
        elif c and d["lanes"] > LANES.get(prev_mode, 5) and c["idle"] >= MODE_WAKE_IDLE and now - last_send >= MODE_WAKE_IDLE:
            why = f"MODE {prev_mode}->{d['mode']}"
        if why and not c:
            log(f"{why} due but no target: {cerr}")
            why = None
    st.update({"at": now, "usage": u, **d, "line": line})
    if why:
        tail = (" the limit reset. Resume the stopped children with the keep-alive line, then go on." if why == "RESET"
                else " HOLD: start nothing new; idle children get a warm-only ping (Reply OK, no tools); running ones"
                     " finish their step." if d["mode"] == "HOLD"
                else " do your heartbeat: keep-alives, merges, spawns within the lane cap.")
        msg = (f"PACER {dt.datetime.now():%H:%M} {why}: {line[11:]}.{tail} Reply in 2 lines."
               " Not this project's coordinator? Reply: not me, and do nothing.")
        if a.send:
            pid = send(c, msg)
            st["last_send"] = now
            log(f"SENT {why} to {c['sid'][:16]} pid={pid}: {line}")
            print(f"TICK SENT {why} -> {c['sid'][:16]}")
        else:
            log(f"WOULD SEND {why} to {c['sid'][:16]}: {line}")
            print(f"TICK would send {why} -> {c['sid'][:16]} (dry run; --send delivers)\n  {safe(msg)}")
    else:
        print("TICK", line)
    STATE.write_text(json.dumps(st), encoding="utf-8")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("claim")
    p.add_argument("--dir")
    p = sub.add_parser("tick")
    p.add_argument("--send", action="store_true")
    a = ap.parse_args()
    if not getattr(a, "json", False):
        DATA.mkdir(parents=True, exist_ok=True)  # --json writes nothing, so it creates nothing either
    return {"status": cmd_status, "claim": cmd_claim, "tick": cmd_tick}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
