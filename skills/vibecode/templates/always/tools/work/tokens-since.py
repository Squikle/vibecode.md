"""Tokens used per opencode session since a moment (default: the start of the current 5-hour usage window).

Usage: python -I -X utf8 tools/work/tokens-since.py [--since ISO] [--anchor H] <name>=<session id> [...]

ctxNow is the last turn's context; COMPACT marks a session over 125k (compact or hand it over). The 5-hour windows
start every 5 hours from `--anchor` (an hour in UTC, default 2: what one account showed; the pacer's pop-up and the
provider's usage page show your real reset, so pass `--since` when they differ). Reads `opencode session export`,
writes nothing.
"""
import datetime
import json
import subprocess
import sys

COMPACT_AT = 125_000


def created(m: dict) -> float:
    for k in ("time", "info"):
        v = m.get(k) or {}
        if isinstance(v, dict):
            c = v.get("created") or (v.get("time") or {}).get("created")
            if c:
                return c
    return 0


def window_start(now: datetime.datetime, anchor: int) -> datetime.datetime:
    w = now.replace(minute=0, second=0, microsecond=0)
    while (w.hour - anchor) % 5:
        w -= datetime.timedelta(hours=1)
    return w


def main(args: list[str]) -> int:
    since, anchor = None, 2
    while args and args[0] in ("--since", "--anchor"):
        if args[0] == "--since":
            since = args[1]
        else:
            anchor = int(args[1])
        args = args[2:]
    if not args or any("=" not in a for a in args):
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    since = since or window_start(datetime.datetime.now(datetime.timezone.utc), anchor).isoformat()
    t0 = datetime.datetime.fromisoformat(since).timestamp() * 1000
    print(f"since {since}")
    rows = []
    for a in args:
        name, sid = a.split("=", 1)
        out = subprocess.run(["opencode", "session", "export", sid], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", shell=True).stdout
        i = out.find("{")
        if i < 0:
            rows.append((name, "no export"))
            continue
        inp = cr = cw = outp = turns = last = 0
        model = ""
        for m in json.loads(out[i:]).get("messages", []):
            if m.get("type") != "assistant" or created(m) < t0:
                continue
            t = m.get("tokens") or {}
            c = t.get("cache") or {}
            inp += t.get("input", 0); cr += c.get("read", 0); cw += c.get("write", 0)
            outp += t.get("output", 0) + t.get("reasoning", 0); turns += 1
            last = t.get("input", 0) + c.get("read", 0) + c.get("write", 0) or last
            md = m.get("model") or {}
            model = f"{md.get('id', '')}#{md.get('variant', '')}" if md else model
        rows.append((name, turns, inp, cr, cw, outp, model, last))
    print(f"{'session':22} {'turns':>5} {'input':>9} {'cacheRd':>11} {'cacheWr':>10} {'output':>8} {'ctxNow':>8}  model")
    tot = [0, 0, 0, 0, 0]
    for r in sorted(rows, key=lambda r: -(r[3] + r[4]) if len(r) > 2 else 0):
        if len(r) == 2:
            print(f"{r[0]:22} {r[1]}")
            continue
        print(f"{r[0]:22} {r[1]:5} {r[2]:9,} {r[3]:11,} {r[4]:10,} {r[5]:8,} {r[7]:8,}  {r[6]}"
              + ("  COMPACT" if r[7] > COMPACT_AT else ""))
        for k in range(5):
            tot[k] += r[k + 1]
    print(f"{'TOTAL':22} {tot[0]:5} {tot[1]:9,} {tot[2]:11,} {tot[3]:10,} {tot[4]:8,}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
