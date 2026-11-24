"""pacer.py: read the Claude plan's usage of its 5 h and 7 d windows and pick a pacing mode.

The pacer plugin (index.js, next to this file) runs `status --json` every 5 min while a slot is paced or global
is on, and on a `/pacer status` when the last read is over 5 min old. Nothing else calls out.

Credentials are read-only: it reads the Claude Code OAuth access token ($CLAUDE_CODE_OAUTH_TOKEN, else
<$CLAUDE_CONFIG_DIR or ~/.claude>/.credentials.json, else the macOS keychain) and never prints, writes or
refreshes it (a refresh rotates the token and can log Claude Code out). An expired token = no reading this time.

  python pacer.py status            one line: usage, pace lines, mode, lane cap (writes state.json)
  python pacer.py status --json     the plugin's machine mode: one JSON object, writes nothing

Week toggle: decide() ignores the week unless toggles.json says "week": true. Off = the week counts as on
pace (BURST allowed). A week limit hit (>= 100 %) still gives LIMITED: the API refuses calls then anyway.

Modes (advisory: the coordinator obeys the lane cap when it starts lanes; nothing is ever stopped or killed):
  GO 5 | EASE 3 | SLOW 2 (ahead of the 5 h or the week's line) | HOLD 0 new (5 h >= 90 %, > 1 h left)
  BURST 7 (< 1 h to the 5 h reset, week on pace: a limit hit now waits < 1 h, so caches stay warm)
  LIMITED 0 (a limit is hit; the first reading after the reset wakes the paced session)
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request

# state and toggles: a per-user data dir, never next to this file (the installed copy lives in the OpenCode
# config dir, which may be synced). $PACER_DATA overrides; default ~/.local/share/pacer (every OS).
DATA = pathlib.Path(os.environ.get("PACER_DATA") or pathlib.Path.home() / ".local" / "share" / "pacer")
STATE = DATA / "state.json"
TOGGLES = pathlib.Path(os.environ.get("PACER_TOGGLES") or DATA / "toggles.json")
URL = "https://api.anthropic.com/api/oauth/usage"
H5, D7 = 5 * 3600, 7 * 86400
SLACK5, SLACK7 = 15.0, 10.0          # points allowed above the straight line
LANES = {"GO": 5, "EASE": 3, "SLOW": 2, "HOLD": 0, "BURST": 7, "LIMITED": 0}


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
    elif platform.system() == "Darwin":  # macOS keeps it in the login keychain (UNVERIFIED there)
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
    # to the nearest minute: the API says 19:49:59.9 for a 19:50 reset
    return dt.datetime.fromtimestamp(round(ts / 60) * 60).strftime(f)


def fmt(u, d, now):
    r5 = _clock(u["h5"]["reset"], "%H:%M") if u["h5"]["reset"] else "?"
    r7 = _clock(u["d7"]["reset"], "%a %m-%d %H:%M") if u["d7"]["reset"] else "?"
    sc = " ".join(f"{k}-week {v}%" for k, v in u["scoped"].items())
    return (f"PACE {dt.datetime.fromtimestamp(now):%H:%M} | 5h {u['h5']['pct']:.0f}% (line {d['line5']:.0f}+{SLACK5:.0f})"
            f" resets {r5} | week {u['d7']['pct']:.0f}% "
            f"{'(line %.0f+%.0f)' % (d['line7'], SLACK7) if d.get('week', True) else '(ignored)'} resets {r7}"
            f"{' | ' + sc if sc else ''} | mode {d['mode']} lanes<={d['lanes']} ({d['why']})")


def cmd_status(a):
    now = time.time()
    u, err = usage()
    if a.json:  # the plugin's machine mode: one JSON line, no files written, never the token
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
    DATA.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"at": now, "usage": u, **d, "line": line}), encoding="utf-8")
    print(line)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args()  # an unknown argument exits 2
    return cmd_status(a)


if __name__ == "__main__":
    sys.exit(main())
